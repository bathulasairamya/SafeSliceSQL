-- ============================================================
-- SafeSliceSQL — SQL Workload for 'world' Database
-- ============================================================
-- Declared workload covering:
--   - Single-table filtering (WHERE)
--   - Grouping & Aggregation (GROUP BY, COUNT, AVG)
--   - Multi-table joins (city <-> country, country <-> countrylanguage)
--   - Join + Filtering
--   - Join + Grouping
-- ============================================================

-- Q1: Filter countries by continent
-- Pattern: SELECT + WHERE
SELECT Name, Continent, Population
FROM country
WHERE Continent = 'Europe';

-- Q2: Filter large metropolitan cities
-- Pattern: SELECT + WHERE
SELECT Name, District, Population
FROM city
WHERE Population > 1000000;

-- Q3: Single-table aggregation by continent
-- Pattern: GROUP BY + COUNT + AVG
SELECT Continent, COUNT(*) AS country_count, AVG(LifeExpectancy) AS avg_life
FROM country
GROUP BY Continent;

-- Q4: Two-table join — cities in Asia
-- Pattern: JOIN + WHERE
SELECT ci.Name AS CityName, ci.Population, co.Name AS CountryName
FROM city ci
JOIN country co ON ci.CountryCode = co.Code
WHERE co.Continent = 'Asia';

-- Q5: Two-table join — official spoken languages
-- Pattern: JOIN + WHERE
SELECT co.Name AS CountryName, cl.Language, cl.Percentage
FROM country co
JOIN countrylanguage cl ON co.Code = cl.CountryCode
WHERE cl.IsOfficial = 'T';

-- Q6: Two-table join with aggregation — city distribution per continent
-- Pattern: JOIN + GROUP BY + COUNT
SELECT co.Continent, COUNT(ci.ID) AS city_count
FROM country co
JOIN city ci ON co.Code = ci.CountryCode
GROUP BY co.Continent;
