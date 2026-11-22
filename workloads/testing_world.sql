-- World: filtered keys, parent joins, and group retention.
SELECT ID, Name, CountryCode, Population
FROM city
WHERE Population >= 1000000;

SELECT cl.CountryCode, cl.Language, co.Name AS country_name
FROM countrylanguage cl
JOIN country co ON cl.CountryCode = co.Code
WHERE cl.IsOfficial = 'T';

SELECT co.Continent, COUNT(*) AS city_count
FROM city ci
JOIN country co ON ci.CountryCode = co.Code
GROUP BY co.Continent;
