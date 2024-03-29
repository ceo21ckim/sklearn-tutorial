from functools import partial

import numpy as np 
import matplotlib.pyplot as plt
import pandas as pd 

from sklearn.datasets import fetch_openml
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import (
    PoissonRegressor, 
    GammaRegressor, 
    TweedieRegressor
)

from sklearn.metrics import (
    mean_tweedie_deviance,
    mean_absolute_error, 
    mean_squared_error, 
    auc
)

from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import (
    FunctionTransformer, 
    OneHotEncoder, 
    StandardScaler, 
    KBinsDiscretizer
)


def load_mtpl2(n_samples = 100000):
    df_freq = fetch_openml(data_id = 41214, as_frame = True)['data']
    df_freq['IDpol'] = df_freq['IDpol'].astype(int)
    df_freq.set_index('IDpol', inplace = True)
    
    df_sev = fetch_openml(data_id = 41215, as_frame = True)['data']
    df_sev = df_sev.groupby('IDpol').sum()
    
    df = df_freq.join(df_sev, how = 'left')
    df['ClaimAmount'].fillna(0, inplace = True)
    
    for column_name in df.columns[df.dtypes.values == object]:
        df[column_name] = df[column_name].str.strip('')
        
    return df.iloc[:n_samples]


def plot_obs_pred(
    df, 
    feature, 
    weight, 
    observed, 
    predicted, 
    y_label = None, 
    title = None, 
    ax = None, 
    fill_legend = False, 
):
    df_ = df.loc[:,[feature, weight]].copy()
    df_['observed'] = df[observed] * df[weight]
    df_['predicted'] = predicted * df[weight]
    df_ = (
        df_.groupby([feature])[[weight, 'observed', 'predicted']]
        .sum()
        .assign(observed = lambda x: x['observed'] / x[weight])
        .assign(predicted = lambda x: x['predicted'] / x[weight])
    )
    
    ax = df_.loc[:,['observed', 'predicted']].plot(style = '.', ax = ax)
    y_max = df_.loc[:, ['observed', 'predicted']].values.max() * 0.8
    p2 = ax.fill_between(
        df_.index, 
        0, 
        y_max * df_[weight] / df_[weight].values.max(), 
        color = 'g', 
        alpha = 0.1, 
    )
    
    if fill_legend:
        ax.legend([p2], ['{} distribution'.format(feature)])
    ax.set(
        ylabel = y_label if y_label is not None else None, 
        title = title if title is not None else 'Train: Observed vs Predicted',
    )
    
def score_estimator(
    estimator, 
    X_train, 
    X_test, 
    df_train,
    df_test, 
    target, 
    weights, 
    tweedie_powers = None, 
):
    metrics = [
        ("D^2 explained", None), 
        ("mean abs. error", mean_absolute_error), 
        ("mean squared error", mean_squared_error)
    ]
    if tweedie_powers:
        metrics += [
            ( "mean Tweedie dev p={:.4f}".format(power),
             partial(mean_tweedie_deviance, power = power), 
             )
            for power in tweedie_powers
        ]
    
    res = []
    for subset_label, X, df in [
        ('train', X_train, df_train), 
        ('test', X_test, df_test)
    ]:
        y, _weights = df[target], df[weights]
        for score_label, metric in metrics:
            if isinstance(estimator, tuple) and len(estimator) == 2:
                est_freq, est_sev = estimator
                y_pred = est_freq.predict(X) * est_sev.predict(X)
            else:
                y_pred = estimator.predict(X)
                
            if metric is None:
                if not hasattr(estimator, 'score'):
                    continue
                score = estimator.score(X, y, sample_weight = _weights)
            
            else:
                score = metric(y, y_pred, sample_weight = _weights)
            
            res.append({'subset' : subset_label, 'metric' : score_label, 'score' : score})
            
    res = (
        pd.DataFrame(res)
        .set_index(['metric', 'subset'])
        .score.unstack(-1)
        .round(4)
        .loc[:,['train', 'test']]
    )
    return res



# Loading datasets, basic feature extraction and target definition

df = load_mtpl2(n_samples = 60000)
df

df.loc[(df['ClaimAmount'] == 0) & (df['ClaimNb'] >= 1), 'ClaimNb'] = 0

df['ClaimNb'] = df['ClaimNb'].clip(upper = 4)  # 상한선
df['Exposure'] = df['Exposure'].clip(upper = 1)
df['ClaimAmount'] = df['ClaimAmount'].clip(upper = 200000)

log_scale_transformer = make_pipeline(
    FunctionTransformer(func = np.log), StandardScaler()
)

column_trans = ColumnTransformer(
    [
        ('binned_numeric', KBinsDiscretizer(n_bins = 10), ['VehAge', 'DrivAge']), 
        (
            'onehot_categorical', 
            OneHotEncoder(), 
            ['VehBrand', 'VehPower', 'VehGas', 'Region', 'Area'], 
        ), 
        ('passthrough_numeric', 'passthrough', ['BonusMalus']), 
        ('log_scaled_numeric', log_scale_transformer, ['Density']), 
    ], 
    remainder = 'drop', 
)

X = column_trans.fit_transform(df)

df['PurePremium'] = df['ClaimAmount'] / df['Exposure']

df['Frequency'] = df['ClaimNb'] / df['Exposure']
df['AvgClaimAmount'] = df['ClaimAmount'] / np.fmax(df['ClaimNb'], 1)

with pd.option_context('display.max_columns', 15):
    print(df[df.ClaimAmount > 0].head())
    
    
# Frequency model - Poisson distribution 

df_train, df_test, X_train, X_test = train_test_split(df, X, random_state = 0)

glm_freq = PoissonRegressor(alpha = 1e-3, max_iter = 400)
glm_freq.fit(X_train, df_train['Frequency'], sample_weight=df_train['Exposure'])

scores = score_estimator(
    glm_freq, 
    X_train, 
    X_test, 
    df_train, 
    df_test, 
    target = 'Frequency', 
    weights = 'Exposure', 
)

print('Evaluation of PoissonRegressor on target Frequency')
print(scores)


fig, ax = plt.subplots(ncols = 2, nrows = 2, figsize = (16, 8))
fig.subplots_adjust(hspace = 0.3, wspace = 0.2)

plot_obs_pred(
    df = df_train, 
    feature = 'DrivAge', 
    weight = 'Exposure', 
    observed = 'Frequency', 
    predicted = glm_freq.predict(X_train), 
    y_label = 'Claim Frequency', 
    title = 'train data', 
    ax = ax[0, 0], 
)

plot_obs_pred(
    df = df_test, 
    feature = 'DrivAge', 
    weight = 'Exposure', 
    observed = 'Frequency', 
    predicted = glm_freq.predict(X_test), 
    y_label = 'Claim Frequency', 
    title = 'test data', 
    ax = ax[0, 1], 
    fill_legend = True, 
)

plot_obs_pred(
    df = df_test, 
    feature = 'VehAge', 
    weight = 'Exposure', 
    observed = 'Frequency', 
    predicted = glm_freq.predict(X_test), 
    y_label = 'Claim Frequency', 
    title = 'test data', 
    ax = ax[1, 0], 
    fill_legend = True, 
)

plot_obs_pred(
    df = df_test, 
    feature = 'BonusMalus', 
    weight = 'Exposure', 
    observed = 'Frequency', 
    predicted = glm_freq.predict(X_test), 
    y_label = 'Claim Frequency', 
    title = 'test data', 
    ax = ax[1, 1], 
    fill_legend = True, 
)

plt.show()

# Severity Model - Gamma distribution 

mask_train = df_train['ClaimAmount'] > 0 
mask_test = df_test['ClaimAmount'] > 0 

glm_sev = GammaRegressor(alpha = 10.0, max_iter = 10000)

glm_sev.fit(
    X_train[mask_train.values], 
    df_train.loc[mask_train, 'AvgClaimAmount'], 
    sample_weight = df_train.loc[mask_train, 'ClaimNb'], 
)

scores = score_estimator(
    glm_sev, 
    X_train[mask_train.values], 
    X_test[mask_test.values], 
    df_train[mask_train], 
    df_test[mask_test], 
    target = 'AvgClaimAmount', 
    weights = 'ClaimNb',
)

print('Evaluation of GammaRegressor on target AvgClaimAmount')
print(scores)



print(
    'Mean AvgClaim Amount per policy:               %.2f'
    % df_train['AvgClaimAmount'].mean()
)

print(
    'Mean AvgClaim Amount | NbClaim > 0:             %.2f'
    % df_train['AvgClaimAmount'][df_train['AvgClaimAmount'] > 0].mean()
)

print(
    'Predicted Mean AvgClaim Amount | NbClaim > 0:   %.2f'
    % glm_sev.predict(X_train).mean()
)


fig, ax = plt.subplots(ncols = 1, nrows = 2, figsize = (16, 6))

plot_obs_pred(
    df = df_train.loc[mask_train], 
    feature = 'DrivAge', 
    weight = 'Exposure', 
    observed = 'AvgClaimAmount', 
    predicted = glm_sev.predict(X_train[mask_train.values]), 
    y_label = 'Average Claim Severity', 
    title = 'train data', 
    ax = ax[0], 
)

plot_obs_pred(
    df = df_test.loc[mask_test], 
    feature = 'DrivAge', 
    weight = 'Exposure', 
    observed = 'AvgClaimAmount', 
    predicted = glm_sev. predict(X_test[mask_test.values]), 
    y_label = 'Average Claim Severity', 
    title = 'test data', 
    ax = ax[1], 
    fill_legend = True, 
)

plt.tight_layout()
plt.show()

# Pure Premium Modeling via a Product Model vs single TweedieRegressor 
glm_pure_premium = TweedieRegressor(power = 1.9, alpha = 0.1, max_iter = 10000)
glm_pure_premium.fit(
    X_train, df_train['PurePremium'], sample_weight = df_train['Exposure']
)

tweedie_powers = [1.5, 1.7, 1.8, 1.9, 1.99, 1.999, 1.9999]

scores_product_model = score_estimator(
    (glm_freq, glm_sev), 
    X_train, 
    X_test, 
    df_train, 
    df_test, 
    target = 'PurePremium', 
    weights = 'Exposure', 
    tweedie_powers = tweedie_powers
)


scores_glm_pure_premium = score_estimator(
    glm_pure_premium, 
    X_train, 
    X_test, 
    df_train, 
    df_test, 
    target = 'PurePremium', 
    weights = 'Exposure', 
    tweedie_powers = tweedie_powers, 
)

scores = pd.concat(
    [scores_product_model, scores_glm_pure_premium], 
    axis = 1, 
    sort = True, 
    keys = ('Product Model', 'TweedieRegressor')
)


print('Evaluation of the Product Model and the Tweedie Regressor on target PurePremium')
with pd.option_context('display.expand_frame_repr', False):
    print(scores)
    
res = []
for subset_label, X, df in [
    ('train', X_train, df_train), 
    ('test', X_test, df_test), 
]:
    exposure = df['Exposure'].values
    res.append(
        {
            'subset': subset_label, 
            'observed': df['ClaimAmount'].values.sum(), 
            'predicted, frequency*severity model': np.sum(
                exposure * glm_freq.predict(X) * glm_sev.predict(X)
            ), 
            'predicted, tweedie, power=%.2f'
            % glm_pure_premium.power: np.sum(exposure * glm_pure_premium.predict(X))
        }
    )
    
print(pd.DataFrame(res).set_index('subset').T)



def lorenz_curve(y_true, y_pred, exposure):
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    exposure = np.asarray(exposure)
    
    ranking = np.argsort(y_pred)
    ranked_exposure = exposure[ranking]
    ranked_pure_premium = y_true[ranking]
    
    cumulated_claim_amount = np.cumsum(ranked_pure_premium * ranked_exposure)
    cumulated_claim_amount /= cumulated_claim_amount[-1]
    cumulated_samples = np.linspace(0, 1, len(cumulated_claim_amount))
    
    return cumulated_samples, cumulated_claim_amount 

fig, ax = plt.subplots(figsize = (8, 8))

y_pred_product = glm_freq.predict(X_test) * glm_sev.predict(X_test)
y_pred_total = glm_pure_premium.predict(X_test)

for label, y_pred in [
    ('Frequency * Severity model', y_pred_product), 
    ('Compound Poisson Gamma', y_pred_total), 
]:
    ordered_samples, cum_claims = lorenz_curve(
        df_test['PurePremium'], y_pred, df_test['Exposure']
    )
    gini = 1 - 2 * auc(ordered_samples, cum_claims)
    label += ' (Gini index: {:.3f}'.format(gini)
    ax.plot(ordered_samples, cum_claims, linestyle = '-', label = label)
    
ordered_samples, cum_claims = lorenz_curve(
    df_test['PurePremium'], df_test['PurePremium'], df_test['Exposure']
)
gini = 1 - 2 * auc(ordered_samples, cum_claims)
label = 'Oracle (Gini index: {:.3f}'.format(gini)
ax.plot(ordered_samples, cum_claims, linestyle = '-.', color = 'gray', label = label)

# Random baseline
ax.plot([0, 1], [0, 1], linestyle = '--', color = 'black', label = 'Random baseline')
ax.set(
    title = 'Lorenz Curves', 
    xlabel = 'Fraction of policyholders \n (ordered by model from safest to riskiest)', 
    ylabel = 'Fraction of total claim amount', 
)

ax.legend(loc = 'upper left')
plt.plot()
plt.show()