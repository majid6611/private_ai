-- Throwaway test database only. The passwords below are dummies for a container that has no
-- published ports and lives on an internal network; never reuse them anywhere.
CREATE DATABASE shop CHARACTER SET utf8mb4;
CREATE USER 'reader'@'%' IDENTIFIED BY 'reader-fixture-pw';
GRANT SELECT ON shop.* TO 'reader'@'%';
CREATE USER 'writer'@'%' IDENTIFIED BY 'writer-fixture-pw';
GRANT SELECT, INSERT, UPDATE ON shop.* TO 'writer'@'%';
CREATE USER 'power'@'%' IDENTIFIED BY 'power-fixture-pw';
GRANT ALL PRIVILEGES ON shop.* TO 'power'@'%' WITH GRANT OPTION;
CREATE USER 'loader'@'%' IDENTIFIED BY 'loader-fixture-pw';
GRANT ALL PRIVILEGES ON shop.* TO 'loader'@'%';
-- A schema the reader must not see through the allow-list tests.
CREATE DATABASE hidden CHARACTER SET utf8mb4;
CREATE TABLE hidden.secrets (id INT PRIMARY KEY, note VARCHAR(40));
INSERT INTO hidden.secrets VALUES (1, 'not for the connector');
GRANT SELECT ON hidden.* TO 'reader'@'%';
