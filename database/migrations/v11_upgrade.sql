-- APEX v11.0 Schema Upgrade

-- Add new columns to signals
ALTER TABLE signals ADD COLUMN IF NOT EXISTS gate_margin DOUBLE PRECISION;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS dynamic_gate DOUBLE PRECISION;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS size_multiplier DOUBLE PRECISION DEFAULT 1.0;

-- (Short columns omitted by user request)

-- Create gate calibration log
CREATE TABLE IF NOT EXISTS gate_calibration_log (
    time TIMESTAMPTZ NOT NULL,
    v7_threshold DOUBLE PRECISION,
    p95_v7_100 DOUBLE PRECISION,
    p95_v7_500 DOUBLE PRECISION,
    signals_passed INT,
    signals_blocked INT
);
SELECT create_hypertable('gate_calibration_log', 'time', if_not_exists => TRUE);
