-- Secure views: the dashboard role can query APP views but not read their
-- definitions (which name restricted tables).
ALTER VIEW APP.OVERVIEW_DAILY SET SECURE;
ALTER VIEW APP.DATA_COVERAGE SET SECURE;
