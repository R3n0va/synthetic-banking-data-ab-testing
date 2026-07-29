/*
===============================================================================
 Project    : Synthetic Banking A/B Testing
 Experiment : EXP-03 Digital Channel Migration
 Purpose    : Export assisted-channel customers and historical migration rates.
 Grain      : One row per customer
===============================================================================
*/

WITH parameters AS
(
    SELECT
        as_of_date,
        as_of_date - 89 AS current_start,
        as_of_date - 269 AS benchmark_feature_start,
        as_of_date - 180 AS benchmark_feature_end,
        as_of_date - 179 AS benchmark_outcome_start,
        as_of_date - 90 AS benchmark_outcome_end
    FROM analytics.v_analysis_parameters
),
benchmark_feature AS
(
    SELECT
        fx.customer_id,
        s.segment_code,
        SUM(
            CASE
                WHEN ch.channel_code IN ('BRANCH', 'TELEPHONE')
                    THEN 1
                ELSE 0
            END
        ) AS assisted_transactions,
        SUM(
            CASE
                WHEN ch.channel_code IN ('BANK_ONLINE', 'MOBILE_BANKING')
                    THEN 1
                ELSE 0
            END
        ) AS digital_transactions
    FROM treasury.fx_transaction AS fx
    JOIN treasury.distribution_channel AS ch
        ON ch.channel_id = fx.channel_id
    JOIN bank.customer AS c
        ON c.customer_id = fx.customer_id
    JOIN treasury.customer_segment AS s
        ON s.customer_segment_id = c.customer_segment_id
    CROSS JOIN parameters AS p
    WHERE fx.transaction_timestamp::DATE
        BETWEEN p.benchmark_feature_start AND p.benchmark_feature_end
    GROUP BY
        fx.customer_id,
        s.segment_code
),
benchmark_dominant_assisted AS
(
    SELECT
        fx.customer_id,
        ch.channel_code,
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
        BETWEEN p.benchmark_feature_start AND p.benchmark_feature_end
      AND ch.channel_code IN ('BRANCH', 'TELEPHONE')
    GROUP BY
        fx.customer_id,
        ch.channel_code
),
benchmark_outcome AS
(
    SELECT
        fx.customer_id,
        BOOL_OR(
            ch.channel_code IN ('BANK_ONLINE', 'MOBILE_BANKING')
        ) AS adopted_digital_channel
    FROM treasury.fx_transaction AS fx
    JOIN treasury.distribution_channel AS ch
        ON ch.channel_id = fx.channel_id
    CROSS JOIN parameters AS p
    WHERE fx.transaction_timestamp::DATE
        BETWEEN p.benchmark_outcome_start AND p.benchmark_outcome_end
    GROUP BY fx.customer_id
),
migration_benchmark AS
(
    SELECT
        bf.segment_code,
        bda.channel_code AS dominant_assisted_channel_code,
        AVG(COALESCE(bo.adopted_digital_channel, FALSE)::INTEGER)
            ::NUMERIC(18, 8) AS historical_digital_adoption_rate
    FROM benchmark_feature AS bf
    JOIN benchmark_dominant_assisted AS bda
        ON bda.customer_id = bf.customer_id
       AND bda.channel_rank = 1
    LEFT JOIN benchmark_outcome AS bo
        ON bo.customer_id = bf.customer_id
    WHERE bf.assisted_transactions >= 2
      AND bf.assisted_transactions > bf.digital_transactions
    GROUP BY
        bf.segment_code,
        bda.channel_code
),
current_activity AS
(
    SELECT
        fx.customer_id,
        COUNT(*) AS prior_fx_transactions,
        SUM(
            CASE
                WHEN ch.channel_code IN ('BRANCH', 'TELEPHONE')
                    THEN 1
                ELSE 0
            END
        ) AS prior_assisted_transactions,
        SUM(
            CASE
                WHEN ch.channel_code IN ('BANK_ONLINE', 'MOBILE_BANKING')
                    THEN 1
                ELSE 0
            END
        ) AS prior_digital_transactions,
        SUM(fx.revenue_amount_usd)::NUMERIC(30, 2)
            AS prior_fx_revenue_usd
    FROM treasury.fx_transaction AS fx
    JOIN treasury.distribution_channel AS ch
        ON ch.channel_id = fx.channel_id
    CROSS JOIN parameters AS p
    WHERE fx.transaction_timestamp::DATE
        BETWEEN p.current_start AND p.as_of_date
    GROUP BY fx.customer_id
),
current_dominant_assisted AS
(
    SELECT
        fx.customer_id,
        ch.channel_code,
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
        BETWEEN p.current_start AND p.as_of_date
      AND ch.channel_code IN ('BRANCH', 'TELEPHONE')
    GROUP BY
        fx.customer_id,
        ch.channel_code
)
SELECT
    c.customer_id,
    s.segment_code,
    b.branch_code,
    cda.channel_code AS dominant_assisted_channel_code,
    ca.prior_fx_transactions,
    ca.prior_assisted_transactions,
    ca.prior_digital_transactions,
    ROUND(
        ca.prior_digital_transactions::NUMERIC
        / NULLIF(ca.prior_fx_transactions, 0),
        6
    ) AS prior_digital_share,
    ca.prior_fx_revenue_usd,
    mb.historical_digital_adoption_rate
FROM current_activity AS ca
JOIN bank.customer AS c
    ON c.customer_id = ca.customer_id
JOIN treasury.customer_segment AS s
    ON s.customer_segment_id = c.customer_segment_id
JOIN bank.branch AS b
    ON b.branch_id = c.branch_id
JOIN current_dominant_assisted AS cda
    ON cda.customer_id = ca.customer_id
   AND cda.channel_rank = 1
JOIN migration_benchmark AS mb
    ON mb.segment_code = s.segment_code
   AND mb.dominant_assisted_channel_code = cda.channel_code
WHERE c.customer_status = 'ACTIVE'
  AND ca.prior_fx_transactions >= 2
  AND ca.prior_assisted_transactions > ca.prior_digital_transactions
ORDER BY c.customer_id;
