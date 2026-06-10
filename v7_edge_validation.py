import pandas as pd
import numpy as np
from scipy import stats
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

try:
    df = pd.read_csv('shadow_trades_database.csv')
except FileNotFoundError:
    print("File shadow_trades_database.csv not found!")
    exit(1)

# Подготовка
df = df[df['status'].isin(['WON', 'LOST'])].copy()

if len(df) == 0:
    print("INSUFFICIENT_DATA: 0 resolved trades found.")
    exit(0)

df['win'] = (df['status'] == 'WON').astype(int)
df['sl_dist'] = ((df['entry_price'] - df['stop_loss'])
                  / df['entry_price'] * 100).abs()
df['tp_dist'] = ((df['take_profit_1'] - df['entry_price'])
                  / df['entry_price'] * 100).abs()
df['rr']      = df['tp_dist'] / df['sl_dist']
df['ev_trade'] = df['win'] * df['tp_dist'] - (1 - df['win']) * df['sl_dist']

print(f"Resolved trades: {len(df)}")
print(f"Overall win rate: {df['win'].mean():.2%}")
print(f"Overall EV/trade: {df['ev_trade'].mean():.3f}%")

# ТЕСТ 1 — МОНОТОННОСТЬ V7 (главный тест)
v7_signals = df[df['v7_score'] != 0].copy()
print(f"\nСигналы с ненулевым V7: {len(v7_signals)}")
print(f"% от всех resolved: {len(v7_signals)/len(df):.1%}")

corr, pval = 0, 1 # defaults

if len(v7_signals) < 50:
    print("INSUFFICIENT_DATA: менее 50 сигналов с V7 != 0")
    print("Вывод: невозможно проверить edge — нужно больше данных после Fix 1")
else:
    bins   = [-50, -20, -10, 0, 10, 20, 30, 40, 48, 100]
    labels = ['<-20','-20:-10','-10:0','0:10','10:20','20:30','30:40','40:48','>=48']
    
    v7_signals['v7_bucket'] = pd.cut(
        v7_signals['v7_score'], bins=bins, labels=labels
    )
    
    bucket_stats = v7_signals.groupby('v7_bucket', observed=True).agg(
        n          = ('win', 'count'),
        win_rate   = ('win', 'mean'),
        avg_ev     = ('ev_trade', 'mean'),
        avg_mfe    = ('mfe_pct', 'mean'),
        avg_mae    = ('mae_pct', 'mean'),
    ).round(4)
    
    print("\n=== V7 BUCKET ANALYSIS ===")
    print(bucket_stats.to_string())
    
    valid = bucket_stats[bucket_stats['n'] >= 5]
    if len(valid) >= 3:
        corr, pval = stats.spearmanr(
            valid.index.codes,
            valid['win_rate']
        )
        print(f"\nSpearman correlation V7→WinRate: {corr:.3f}")
        print(f"P-value: {pval:.4f}")
        
        if corr > 0.5 and pval < 0.05:
            print("✅ МОНОТОННОСТЬ ПОДТВЕРЖДЕНА: V7 score предсказывает win rate")
        elif corr > 0.3 and pval < 0.10:
            print("⚠️  СЛАБАЯ СВЯЗЬ: есть намёк на edge, нужно больше данных")
        else:
            print("❌ СВЯЗИ НЕТ: V7 score НЕ предсказывает win rate")
    
    # График
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle('V7 Score Edge Validation', fontsize=14, fontweight='bold')
    
    valid.plot(y='win_rate', kind='bar', ax=axes[0],
               color=['red' if x < 0.25 else 'orange' if x < 0.35
                      else 'green' for x in valid['win_rate']],
               legend=False)
    axes[0].set_title('Win Rate by V7 Bucket')
    axes[0].set_ylabel('Win Rate')
    axes[0].axhline(y=df['win'].mean(), color='blue',
                    linestyle='--', label='Overall WR')
    axes[0].legend()
    
    valid.plot(y='avg_ev', kind='bar', ax=axes[1],
               color=['green' if x > 0 else 'red' for x in valid['avg_ev']],
               legend=False)
    axes[1].set_title('Expected Value by V7 Bucket (%)')
    axes[1].axhline(y=0, color='black', linestyle='-', linewidth=0.8)
    
    axes[2].scatter(v7_signals['v7_score'], v7_signals['win'],
                    alpha=0.3, s=10, color='steelblue')
    axes[2].set_title('V7 Score vs Win (scatter)')
    axes[2].set_xlabel('V7 Score')
    axes[2].set_ylabel('Win (1) / Loss (0)')
    
    plt.tight_layout()
    plt.savefig('v7_edge_test1_monotonicity.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("\nСохранено: v7_edge_test1_monotonicity.png")

# ТЕСТ 2 — STATISTICAL SIGNIFICANCE
sig_chi, sig_t, ci_pos = False, False, False

if len(v7_signals) >= 50:
    median_v7 = v7_signals['v7_score'].median()
    
    high_v7 = v7_signals[v7_signals['v7_score'] >  median_v7]['win']
    low_v7  = v7_signals[v7_signals['v7_score'] <= median_v7]['win']
    
    print(f"\n=== HIGH vs LOW V7 SPLIT ===")
    print(f"Median V7: {median_v7:.1f}")
    print(f"High V7 (n={len(high_v7)}): WR = {high_v7.mean():.2%}")
    print(f"Low  V7 (n={len(low_v7)}):  WR = {low_v7.mean():.2%}")
    
    high_wins   = high_v7.sum()
    high_losses = len(high_v7) - high_wins
    low_wins    = low_v7.sum()
    low_losses  = len(low_v7) - low_wins
    
    contingency = [[high_wins, high_losses],
                   [low_wins,  low_losses]]
    chi2, pval_chi, _, _ = stats.chi2_contingency(contingency)
    
    print(f"\nChi-square test:")
    print(f"  chi2 = {chi2:.3f}, p = {pval_chi:.4f}")
    
    high_ev = v7_signals[v7_signals['v7_score'] >  median_v7]['ev_trade']
    low_ev  = v7_signals[v7_signals['v7_score'] <= median_v7]['ev_trade']
    t_stat, pval_t = stats.ttest_ind(high_ev, low_ev)
    
    print(f"\nT-test EV (high V7 vs low V7):")
    print(f"  High EV mean = {high_ev.mean():.3f}%")
    print(f"  Low  EV mean = {low_ev.mean():.3f}%")
    print(f"  t = {t_stat:.3f}, p = {pval_t:.4f}")
    
    np.random.seed(42)
    n_boot = 5000
    
    boot_diff = []
    for _ in range(n_boot):
        h = high_v7.sample(len(high_v7), replace=True).mean() if len(high_v7) > 0 else 0
        l = low_v7.sample(len(low_v7),   replace=True).mean() if len(low_v7) > 0 else 0
        boot_diff.append(h - l)
    
    ci_low  = np.percentile(boot_diff, 2.5)
    ci_high = np.percentile(boot_diff, 97.5)
    
    print(f"\nBootstrap 95% CI for WR difference:")
    print(f"  [{ci_low:.3f}, {ci_high:.3f}]")
    
    if ci_low > 0:
        print("✅ CI полностью выше 0: высокий V7 статистически лучше")
    elif ci_high < 0:
        print("❌ CI полностью ниже 0: высокий V7 статистически ХУЖЕ")
    else:
        print("⚠️  CI пересекает 0: разница статистически незначима")
    
    sig_chi = pval_chi < 0.05
    sig_t   = pval_t   < 0.05
    ci_pos  = ci_low   > 0
    
    print(f"\nP-values: chi2={'✅' if sig_chi else '❌'} "
          f"t-test={'✅' if sig_t else '❌'} "
          f"bootstrap_CI={'✅' if ci_pos else '❌'}")

# ТЕСТ 3 — BLOCK FILTER VALUE
df_all = pd.read_csv('shadow_trades_database.csv')
df_all['sl_dist'] = ((df_all['entry_price'] - df_all['stop_loss'])
                      / df_all['entry_price'] * 100).abs()
df_all['tp_dist'] = ((df_all['take_profit_1'] - df_all['entry_price'])
                      / df_all['entry_price'] * 100).abs()

print("\n=== FILTER VALUE BY BLOCK REASON ===")
print(f"{'Block Reason':<25} {'N':>6} {'WR':>7} {'EV/trade':>10} "
      f"{'Filter Value':>14}")
print("-" * 65)

for reason in df_all['block_reason'].unique():
    sub = df_all[df_all['block_reason'] == reason]
    res = sub[sub['status'].isin(['WON', 'LOST'])]
    if len(res) < 20:
        continue
    
    wr      = (res['status'] == 'WON').mean()
    avg_tp  = res['tp_dist'].mean()
    avg_sl  = res['sl_dist'].mean()
    ev      = wr * avg_tp - (1 - wr) * avg_sl
    
    filter_val = -ev * len(res)
    fv_per_trade = -ev
    
    icon = '✅' if ev < -0.3 else '⚠️ ' if ev < 0 else '❌'
    print(f"{icon} {str(reason):<23} {len(res):>6} {wr:>6.1%} "
          f"{ev:>+9.3f}%  {fv_per_trade:>+12.3f}%")

print("\nИнтерпретация Filter Value:")
print("  Положительный = правильно заблокировали (EV отрицательный)")
print("  Отрицательный = ошибочно заблокировали хорошие сделки")

# ТЕСТ 4 — SYMBOL EDGE STABILITY
sym = df.groupby('symbol').agg(
    n        = ('win', 'count'),
    win_rate = ('win', 'mean'),
    avg_ev   = ('ev_trade', 'mean')
).query('n >= 10')

print(f"\n=== SYMBOL EDGE DISTRIBUTION ===")
print(f"Символов с >=10 resolved сделок: {len(sym)}")
if len(sym) > 0:
    print(f"Символов с положительным EV: "
          f"{(sym['avg_ev']>0).sum()} ({(sym['avg_ev']>0).mean():.1%})")
    print(f"Символов с WR > 35%: "
          f"{(sym['win_rate']>0.35).sum()} ({(sym['win_rate']>0.35).mean():.1%})")
    print(f"Символов с WR < 20%: "
          f"{(sym['win_rate']<0.20).sum()} ({(sym['win_rate']<0.20).mean():.1%})")

    sym['ev_total']  = sym['avg_ev'] * sym['n']
    positive         = sym[sym['ev_total'] > 0]
    if len(positive) > 0:
        shares       = positive['ev_total'] / positive['ev_total'].sum()
        herfindahl   = (shares ** 2).sum()
        print(f"\nHerfindahl concentration index: {herfindahl:.3f}")
        print("  < 0.15 → edge распределён (хорошо)")
        print("  > 0.30 → 1-2 символа делают всю прибыль (опасно)")
        
        if herfindahl > 0.30:
            print("❌ EDGE CONCENTRATED: система зависит от пары символов")
        else:
            print("✅ EDGE DISTRIBUTED: работает на многих символах")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    sym['win_rate'].hist(bins=20, ax=axes[0],
                         color='steelblue', edgecolor='black', alpha=0.7)
    axes[0].axvline(x=0.35, color='green', linestyle='--',
                    label='Break-even WR (R:R=2.5)')
    axes[0].axvline(x=sym['win_rate'].mean(), color='red',
                    linestyle='--', label=f"Mean={sym['win_rate'].mean():.1%}")
    axes[0].set_title('Win Rate Distribution Across Symbols')
    axes[0].set_xlabel('Win Rate')
    axes[0].legend(fontsize=8)

    axes[1].scatter(sym['n'], sym['win_rate'],
                    c=sym['avg_ev'], cmap='RdYlGn',
                    s=60, alpha=0.8, vmin=-3, vmax=3)
    axes[1].set_title('Win Rate vs Sample Size (color=EV)')
    axes[1].set_xlabel('Sample Size (n resolved)')
    axes[1].set_ylabel('Win Rate')
    axes[1].axhline(y=0.35, color='green', linestyle='--', alpha=0.5)

    plt.tight_layout()
    plt.savefig('v7_edge_test4_symbols.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("\nСохранено: v7_edge_test4_symbols.png")

# ТЕСТ 5 — BREAK-EVEN WIN RATE CHECK
print("\n=== BREAK-EVEN ANALYSIS ===")

rr_buckets = df.groupby(
    pd.cut(df['rr'], bins=[0, 1.5, 2.0, 2.5, 3.0, 4.0, 100],
           labels=['<1.5','1.5-2','2-2.5','2.5-3','3-4','>4'])
).agg(
    n            = ('win', 'count'),
    actual_wr    = ('win', 'mean'),
    avg_rr       = ('rr',  'mean'),
).round(3)

rr_buckets['breakeven_wr'] = (1 / (1 + rr_buckets['avg_rr'])).round(3)
rr_buckets['wr_gap']       = (rr_buckets['actual_wr']
                               - rr_buckets['breakeven_wr']).round(3)
rr_buckets['profitable']   = rr_buckets['wr_gap'] > 0

print(rr_buckets[['n','actual_wr','breakeven_wr','wr_gap','profitable']].to_string())
print()

profitable_buckets = rr_buckets['profitable'].sum()
print(f"R:R бакетов с положительным gap: {profitable_buckets}/{len(rr_buckets)}")
if profitable_buckets > len(rr_buckets) // 2:
    print("✅ Большинство R:R групп прибыльны")
else:
    print("❌ Большинство R:R групп убыточны — проблема в win rate")

# ФИНАЛЬНЫЙ ВЕРДИКТ
print("\n" + "="*60)
print("     APEX V7 EDGE VALIDATION — ФИНАЛЬНЫЙ ВЕРДИКТ")
print("="*60)

results = {}

if len(v7_signals) >= 50:
    results['monotonicity'] = corr > 0.3 and pval < 0.10
else:
    results['monotonicity'] = None

if len(v7_signals) >= 50:
    results['significance'] = sig_chi or (sig_t and ci_pos)
else:
    results['significance'] = None

if len(sym) >= 10:
    results['distribution'] = (sym['win_rate'] > 0.35).mean() > 0.25
else:
    results['distribution'] = None

results['breakeven'] = profitable_buckets > len(rr_buckets) // 2

passed    = sum(1 for v in results.values() if v == True)
failed    = sum(1 for v in results.values() if v == False)
unknown   = sum(1 for v in results.values() if v is None)

print()
for test, result in results.items():
    icon = '✅' if result == True else ('❌' if result == False else '❓')
    print(f"  {icon} {test}")

print()
print(f"Пройдено: {passed} | Провалено: {failed} | Нет данных: {unknown}")
print()

if unknown >= 2:
    verdict = "INSUFFICIENT_DATA"
    msg = ("Недостаточно данных для вывода. "
           "Запусти Fix 1 (V7 gate), собери 500+ resolved сделок, "
           "затем запусти этот тест снова.")

elif passed >= 4:
    verdict = "EDGE_CONFIRMED"
    msg = ("V7 score имеет измеримый предсказательный edge. "
           "Система готова к расширенному shadow с осторожным "
           "уменьшением gate. Не запускать live capital до "
           "300+ resolved shadow сделок после Fix 1.")

elif passed >= 2:
    verdict = "EDGE_WEAK"
    msg = ("Слабый сигнал edge. V7 работает на части символов "
           "но не системно. Требуется: Fix 1 + Fix 2 + 300+ сделок "
           "и повторный тест. Live capital преждевременен.")

else:
    verdict = "EDGE_UNCONFIRMED"
    msg = ("V7 score не показывает измеримого edge на текущих данных. "
           "Необходимо: пересмотреть alpha sources, заменить SMC "
           "на factor-based модель (Фаза 2.1 из мастер-плана), "
           "запустить ML модель (Фаза 2.2). "
           "Live capital категорически преждевременен.")

print(f"  ВЕРДИКТ: {verdict}")
print(f"\n  {msg}")
print()
print("Сохранённые графики:")
print("  v7_edge_test1_monotonicity.png — ключевой график")
print("  v7_edge_test4_symbols.png      — распределение по символам")
print("="*60)
