-- Chinook MySQL edition: preserve the upstream table-name capitalization.
SELECT il.InvoiceLineId, i.InvoiceId, c.CustomerId, t.TrackId, il.Quantity
FROM InvoiceLine il
JOIN Invoice i ON il.InvoiceId = i.InvoiceId
JOIN Customer c ON i.CustomerId = c.CustomerId
JOIN Track t ON il.TrackId = t.TrackId
WHERE c.Country = 'USA';

SELECT pt.PlaylistId, pt.TrackId, p.Name AS playlist_name, t.Name AS track_name
FROM PlaylistTrack pt
JOIN Playlist p ON pt.PlaylistId = p.PlaylistId
JOIN Track t ON pt.TrackId = t.TrackId
WHERE p.PlaylistId = 1;

SELECT g.GenreId, g.Name, COUNT(*) AS track_count
FROM Track t
JOIN Genre g ON t.GenreId = g.GenreId
GROUP BY g.GenreId, g.Name;
