-- DHAN STRATEGY ROUTER — Supabase Schema
CREATE TABLE IF NOT EXISTS market_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE        NOT NULL UNIQUE,
    vix             NUMERIC(6,2),
    vix_chg_pct     NUMERIC(6,2),
    nifty           NUMERIC(8,2),
    nifty_chg_pct   NUMERIC(6,2),
    pcr             NUMERIC(5,3),
    ret_20d         NUMERIC(6,2),
    dma_50          NUMERIC(8,2),
    above_dma50     BOOLEAN,
    regime          TEXT,
    sp500           NUMERIC(8,2),
    sp500_chg_pct   NUMERIC(6,2),
    nasdaq_chg_pct  NUMERIC(6,2),
    dxy             NUMERIC(6,2),
    crude_oil       NUMERIC(7,2),
    gold            NUMERIC(8,2),
    us_vix          NUMERIC(6,2),
    fear_greed      INTEGER,
    sgx_nifty       NUMERIC(8,2),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS strategy_verdicts (
    id              BIGSERIAL PRIMARY KEY,
    verdict_date    DATE        NOT NULL UNIQUE,
    zen_score INTEGER, curv_score INTEGER, damp_score INTEGER,
    winner TEXT, verdict_text TEXT, reason TEXT, signal_strength TEXT,
    gap INTEGER, regime TEXT, zen_streak INTEGER, curv_streak INTEGER,
    damp_streak INTEGER, followed BOOLEAN, actual_pnl NUMERIC(10,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS capital_log (
    id BIGSERIAL PRIMARY KEY, month TEXT NOT NULL UNIQUE,
    active_strategy TEXT, regime TEXT, trade_count INTEGER,
    wins INTEGER, losses INTEGER, gross_pnl NUMERIC(10,2),
    withdrawn_30pct NUMERIC(10,2), reinvested_70pct NUMERIC(10,2),
    portfolio_after NUMERIC(10,2), cum_withdrawn NUMERIC(10,2),
    notes TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS strategy_momentum (
    id BIGSERIAL PRIMARY KEY, updated_date DATE NOT NULL UNIQUE,
    zen_streak INTEGER, zen_last5_wins INTEGER, zen_total_pnl NUMERIC(12,2),
    curv_streak INTEGER, curv_last5_wins INTEGER, curv_total_pnl NUMERIC(12,2),
    damp_streak INTEGER, damp_last5_wins INTEGER, damp_total_pnl NUMERIC(12,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON market_snapshots(snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_verdicts_date ON strategy_verdicts(verdict_date DESC);
CREATE INDEX IF NOT EXISTS idx_capital_month ON capital_log(month DESC);
ALTER TABLE market_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_verdicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE capital_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_momentum ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON market_snapshots FOR ALL USING (true);
CREATE POLICY "service_role_all" ON strategy_verdicts FOR ALL USING (true);
CREATE POLICY "service_role_all" ON capital_log FOR ALL USING (true);
CREATE POLICY "service_role_all" ON strategy_momentum FOR ALL USING (true);
