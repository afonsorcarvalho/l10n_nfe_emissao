-- Adiciona coluna nfe_environment em res_company (corrige UndefinedColumn no upgrade)
-- Execute quando o erro "column res_company.nfe_environment does not exist" ocorrer.
-- Uso: psql -U odoo -d SEU_BANCO_ODOO -f add_nfe_environment_column.sql

-- PostgreSQL 9.6+: IF NOT EXISTS evita erro se a coluna já existir
ALTER TABLE res_company ADD COLUMN IF NOT EXISTS nfe_environment VARCHAR;

UPDATE res_company SET nfe_environment = '2' WHERE nfe_environment IS NULL;
