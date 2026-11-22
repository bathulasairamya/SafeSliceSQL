-- Import diamonds.csv alone; otherwise substitute the manifest's table name.
SELECT _safeslice_id, carat, cut, color, price
FROM data_1_diamonds
WHERE cut = 'Ideal';

SELECT cut, COUNT(*) AS diamond_count
FROM data_1_diamonds
GROUP BY cut;
