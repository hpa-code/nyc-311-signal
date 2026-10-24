-- Feed for the Tableau Public companion (ADR 0002). Counts only, one row per
-- creation day, borough, agency, Problem and channel: it leaves the warehouse
-- as a CSV, so it carries no unique_key, address or location below borough.
CREATE OR REPLACE SECURE VIEW APP.TABLEAU_DAILY COPY GRANTS AS
SELECT
  created_date_local                          AS created_date,
  COALESCE(borough, 'Unspecified')            AS borough,
  COALESCE(agency, 'Unspecified')             AS agency,
  COALESCE(problem, 'Unspecified')            AS problem,
  COALESCE(open_data_channel_type, 'UNKNOWN') AS channel,
  COUNT(*)                                    AS requests,
  COUNT_IF(status = 'Closed')                 AS closed_requests,
  COUNT_IF(status IS DISTINCT FROM 'Closed')  AS open_requests
FROM CLEAN.SERVICE_REQUESTS
GROUP BY ALL;

GRANT SELECT ON VIEW APP.TABLEAU_DAILY TO ROLE {{dashboard_role}};
