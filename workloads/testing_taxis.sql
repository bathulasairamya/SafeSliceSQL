-- Import taxis.csv alone; otherwise substitute the manifest's table name.
SELECT _safeslice_id, pickup, dropoff, payment, total
FROM data_1_taxis
WHERE payment = 'credit card';

SELECT pickup_borough, COUNT(*) AS trip_count
FROM data_1_taxis
GROUP BY pickup_borough;
