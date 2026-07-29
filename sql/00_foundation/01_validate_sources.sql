/*
===============================================================================
 Project  : Synthetic Banking A/B Testing
 Layer    : 00 Foundation
 File     : 01_validate_sources.sql
 Purpose  : Validate the required upstream source tables and analytical views.
 Database : PostgreSQL 17 / synthetic_banking_sql
===============================================================================
*/

DO
$$
DECLARE
    missing_objects TEXT;
BEGIN
    WITH required_objects(object_name) AS
    (
        VALUES
            ('bank.customer'),
            ('bank.branch'),
            ('treasury.customer_segment'),
            ('treasury.distribution_channel'),
            ('treasury.fx_transaction'),
            ('treasury.dcd_contract'),
            ('treasury.dcd_master_agreement'),
            ('analytics.v_analysis_parameters')
    )
    SELECT STRING_AGG(object_name, ', ' ORDER BY object_name)
    INTO missing_objects
    FROM required_objects
    WHERE TO_REGCLASS(object_name) IS NULL;

    IF missing_objects IS NOT NULL THEN
        RAISE EXCEPTION
            'Required upstream objects are missing: %',
            missing_objects;
    END IF;
END
$$;
