/*
===============================================================================
 Project    : Synthetic Banking A/B Testing
 Experiment : EXP-02 DCD Cross-Sell Outreach
 Purpose    : Export operationally eligible FX-only customers and a historical
              segment conversion benchmark.
 Grain      : One row per customer
 Database   : PostgreSQL 17 / synthetic_banking_sql
===============================================================================
*/

WITH parameters AS
(
    SELECT
        as_of_date,
        as_of_date - 45 AS historical_cutoff,
        as_of_date - 224 AS historical_preperiod_start,
        as_of_date - 179 AS current_preperiod_start
    FROM analytics.v_analysis_parameters
),
historical_effective_agreement AS
(
    SELECT DISTINCT
        ma.customer_id
    FROM treasury.dcd_master_agreement AS ma
    CROSS JOIN parameters AS p
    WHERE ma.signed_on <= p.historical_cutoff
      AND (
            ma.terminated_on IS NULL
            OR ma.terminated_on > p.historical_cutoff
      )
),
historical_fx_population AS
(
    SELECT DISTINCT
        fx.customer_id
    FROM treasury.fx_transaction AS fx
    CROSS JOIN parameters AS p
    WHERE fx.transaction_timestamp::DATE
        BETWEEN p.historical_preperiod_start AND p.historical_cutoff
),
historical_eligible AS
(
    SELECT
        c.customer_id,
        s.segment_code
    FROM historical_fx_population AS hfx
    JOIN bank.customer AS c
        ON c.customer_id = hfx.customer_id
    JOIN treasury.customer_segment AS s
        ON s.customer_segment_id = c.customer_segment_id
    JOIN historical_effective_agreement AS hea
        ON hea.customer_id = c.customer_id
    CROSS JOIN parameters AS p
    WHERE c.opened_on <= p.historical_cutoff
      AND (
            c.closed_on IS NULL
            OR c.closed_on > p.historical_cutoff
      )
      AND NOT EXISTS
          (
              SELECT 1
              FROM treasury.dcd_contract AS dcd
              WHERE dcd.customer_id = c.customer_id
                AND dcd.contract_status <> 'CANCELLED'
                AND dcd.contract_created_at::DATE <= p.historical_cutoff
          )
),
historical_outcome AS
(
    SELECT DISTINCT
        dcd.customer_id
    FROM treasury.dcd_contract AS dcd
    CROSS JOIN parameters AS p
    WHERE dcd.contract_status <> 'CANCELLED'
      AND dcd.contract_created_at::DATE
        BETWEEN p.historical_cutoff + 1 AND p.as_of_date
),
segment_benchmark AS
(
    SELECT
        he.segment_code,
        AVG((ho.customer_id IS NOT NULL)::INTEGER)::NUMERIC(18, 8)
            AS historical_dcd_conversion_rate
    FROM historical_eligible AS he
    LEFT JOIN historical_outcome AS ho
        ON ho.customer_id = he.customer_id
    GROUP BY he.segment_code
),
segment_fee AS
(
    SELECT
        s.segment_code,
        AVG(dcd.fee_amount_usd)::NUMERIC(30, 2)
            AS average_dcd_fee_usd
    FROM treasury.dcd_contract AS dcd
    JOIN bank.customer AS c
        ON c.customer_id = dcd.customer_id
    JOIN treasury.customer_segment AS s
        ON s.customer_segment_id = c.customer_segment_id
    WHERE dcd.contract_status <> 'CANCELLED'
      AND dcd.fee_amount_usd > 0
    GROUP BY s.segment_code
),
current_effective_agreement AS
(
    SELECT DISTINCT
        ma.customer_id
    FROM treasury.dcd_master_agreement AS ma
    CROSS JOIN parameters AS p
    WHERE ma.agreement_status = 'ACTIVE'
      AND ma.signed_on <= p.as_of_date
      AND (
            ma.terminated_on IS NULL
            OR ma.terminated_on > p.as_of_date
      )
),
fx_preperiod AS
(
    SELECT
        fx.customer_id,
        COUNT(*) AS prior_fx_transactions,
        SUM(
            fx.source_amount
            * fx.market_rate
            * fx.revenue_conversion_rate_to_usd
        )::NUMERIC(30, 2) AS prior_fx_turnover_usd,
        SUM(fx.revenue_amount_usd)::NUMERIC(30, 2)
            AS prior_fx_revenue_usd
    FROM treasury.fx_transaction AS fx
    CROSS JOIN parameters AS p
    WHERE fx.transaction_timestamp::DATE
        BETWEEN p.current_preperiod_start AND p.as_of_date
    GROUP BY fx.customer_id
)
SELECT
    c.customer_id,
    s.segment_code,
    b.branch_code,
    fp.prior_fx_transactions,
    fp.prior_fx_turnover_usd,
    fp.prior_fx_revenue_usd,
    sb.historical_dcd_conversion_rate,
    sf.average_dcd_fee_usd
FROM fx_preperiod AS fp
JOIN bank.customer AS c
    ON c.customer_id = fp.customer_id
JOIN treasury.customer_segment AS s
    ON s.customer_segment_id = c.customer_segment_id
JOIN bank.branch AS b
    ON b.branch_id = c.branch_id
JOIN current_effective_agreement AS cea
    ON cea.customer_id = c.customer_id
JOIN segment_benchmark AS sb
    ON sb.segment_code = s.segment_code
JOIN segment_fee AS sf
    ON sf.segment_code = s.segment_code
WHERE c.customer_status = 'ACTIVE'
  AND fp.prior_fx_transactions >= 1
  AND fp.prior_fx_turnover_usd > 0
  AND fp.prior_fx_revenue_usd > 0
  AND NOT EXISTS
      (
          SELECT 1
          FROM treasury.dcd_contract AS dcd
          WHERE dcd.customer_id = c.customer_id
            AND dcd.contract_status <> 'CANCELLED'
      )
ORDER BY c.customer_id;
