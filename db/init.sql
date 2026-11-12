---- One database per microservice (database-per-service pattern)
--CREATE DATABASE auth_db;
--CREATE DATABASE ops_db;
--CREATE DATABASE report_db;
--CREATE DATABASE media_db;
--CREATE DATABASE chat_db;
--CREATE DATABASE notify_db;


-- One database per microservice (database-per-service pattern)
-- Runs only the first time the Postgres volume is created.
CREATE DATABASE auth_db;
CREATE DATABASE ops_db;
CREATE DATABASE report_db;
CREATE DATABASE media_db;
CREATE DATABASE chat_db;
CREATE DATABASE notify_db;