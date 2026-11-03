-- ADMIN-REVIEWED EXAMPLE for MySQL 8.0.18+; never executed by the app.
-- Replace SOURCE below with the real source schema.
-- Prefix grants below require @@partial_revokes = OFF. If it is ON, provision
-- exact per-job schema grants with your administrator instead of global grants.
-- Escape any literal _ or % in a source database name with \ in GRANT.
-- MySQL generates passwords and returns them to this client's result grid.
SELECT @@partial_revokes AS verify_prefix_grants_supported;

CREATE USER 'safeslice_reader'@'localhost' IDENTIFIED BY RANDOM PASSWORD;
CREATE USER 'safeslice_writer'@'localhost' IDENTIFIED BY RANDOM PASSWORD;

GRANT SELECT, SHOW VIEW ON `SOURCE`.* TO 'safeslice_reader'@'localhost';
GRANT SELECT, INSERT, DELETE, CREATE, DROP, REFERENCES, INDEX
    ON `safeslice\_input\_%`.* TO 'safeslice_writer'@'localhost';
GRANT SELECT, INSERT, DELETE, CREATE, DROP, REFERENCES, INDEX
    ON `safeslice\_output\_%`.* TO 'safeslice_writer'@'localhost';

-- Optional generated-database checks: separate test server/account recommended.
-- GRANT SELECT, INSERT, DELETE, CREATE, DROP, REFERENCES, INDEX
--     ON `safeslice\_check\_%`.* TO 'safeslice_writer'@'localhost';
