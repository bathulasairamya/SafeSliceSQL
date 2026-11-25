-- Fictional teaching input. Run only in a fresh empty database.
CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, customer_name VARCHAR(40) NOT NULL);
CREATE TABLE products (product_id INTEGER PRIMARY KEY, product_name VARCHAR(40) NOT NULL, unit_price INTEGER NOT NULL);
CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, FOREIGN KEY(customer_id) REFERENCES customers(customer_id));
CREATE TABLE order_lines (line_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity INTEGER NOT NULL, FOREIGN KEY(order_id) REFERENCES orders(order_id), FOREIGN KEY(product_id) REFERENCES products(product_id));
INSERT INTO customers VALUES (1,'Asha'),(2,'Bharat'),(3,'Charu');
INSERT INTO products VALUES (10,'Notebook',50),(20,'Pen',10),(30,'Bag',500);
INSERT INTO orders VALUES (101,1),(102,1),(103,2);
INSERT INTO order_lines VALUES (1001,101,10,2),(1002,101,20,5),(1003,102,30,1),(1004,103,20,10);
