-- Q1: notebook sale details.
SELECT l.line_id, p.product_name, l.quantity
FROM order_lines l JOIN products p ON l.product_id = p.product_id
WHERE p.product_name = 'Notebook';
-- Q2: all sale-line records.
SELECT line_id, order_id, product_id, quantity FROM order_lines;
-- Q3: number of sale lines for each product.
SELECT p.product_id, COUNT(*) AS line_count
FROM products p JOIN order_lines l ON p.product_id = l.product_id
GROUP BY p.product_id;
-- Q4: total number of sale-line records.
SELECT COUNT(*) AS total_lines FROM order_lines;
-- Q5: a deliberately nonexistent customer.
SELECT customer_id, customer_name FROM customers WHERE customer_id = 999;
