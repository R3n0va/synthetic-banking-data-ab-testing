/*
===============================================================================
 Project    : Synthetic Banking A/B Testing
 Experiment : EXP-01 Live FX Spread Discount
 Purpose    : Export the eligible customer population and pre-period covariates.
 Grain      : One row per customer
===============================================================================
*/

WITH parameters AS
(
    SELECT
        as_of_date,
        activity_window_start
    FROM analytics.v_analysis_parameters
),
fx_preperiod AS
(
    SELECT
        fx.customer_id,
        COUNT(*) AS prior_fx_transactions,
        COUNT(DISTINCT fx.transaction_timestamp::DATE) AS prior_fx_active_days,
        SUM(
            fx.source_amount
            * fx.market_rate
            * fx.revenue_conversion_rate_to_usd
        )::NUMERIC(30, 2) AS prior_fx_turnover_usd,
        SUM(fx.revenue_amount_usd)::NUMERIC(30, 2) AS prior_fx_revenue_usd,
        AVG(fx.spread)::NUMERIC(18, 8) AS prior_avg_spread
    FROM treasury.fx_transaction AS fx
    CROSS JOIN parameters AS p
    WHERE fx.transaction_timestamp::DATE
        BETWEEN p.activity_window_start AND p.as_of_date
    GROUP BY fx.customer_id
),
channel_counts AS
(
    SELECT
        fx.customer_id,
        ch.channel_code,
        COUNT(*) AS channel_transactions,
        ROW_NUMBER() OVER
        (
            PARTITION BY fx.customer_id
            ORDER BY COUNT(*) DESC, ch.channel_code
        ) AS channel_rank
    FROM treasury.fx_transaction AS fx
    JOIN treasury.distribution_channel AS ch
        ON ch.channel_id = fx.channel_id
    CROSS JOIN parameters AS p
    WHERE fx.transaction_timestamp::DATE
        BETWEEN p.activity_window_start AND p.as_of_date
    GROUP BY
        fx.customer_id,
        ch.channel_code
)
SELECT
    c.customer_id,
    s.segment_code,
    b.branch_code,
    cc.channel_code AS dominant_channel_code,
    fp.prior_fx_transactions,
    fp.prior_fx_active_days,
    fp.prior_fx_turnover_usd,
    fp.prior_fx_revenue_usd,
    ROUND(
        fp.prior_fx_turnover_usd
        / NULLIF(fp.prior_fx_transactions, 0),
        2
    ) AS prior_avg_ticket_usd,
    fp.prior_avg_spread,
    ROUND(
        fp.prior_fx_revenue_usd
        / NULLIF(fp.prior_fx_turnover_usd, 0)
        * 10000,
        4
    ) AS prior_revenue_yield_bps
FROM fx_preperiod AS fp
JOIN bank.customer AS c
    ON c.customer_id = fp.customer_id
JOIN treasury.customer_segment AS s
    ON s.customer_segment_id = c.customer_segment_id
JOIN bank.branch AS b
    ON b.branch_id = c.branch_id
JOIN channel_counts AS cc
    ON cc.customer_id = fp.customer_id
   AND cc.channel_rank = 1
WHERE c.customer_status = 'ACTIVE'
  AND fp.prior_fx_transactions >= 2
  AND fp.prior_fx_turnover_usd > 0
  AND fp.prior_fx_revenue_usd > 0
ORDER BY c.customer_id;
