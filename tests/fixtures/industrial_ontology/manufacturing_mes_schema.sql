-- Generic manufacturing MES schema sample for ontology discovery and extraction.
-- Status conventions:
-- machine.status: 0=offline, 1=idle, 2=running, 3=alarm, 4=maintenance
-- alarm_event.severity: 1=info, 2=warning, 3=major, 4=critical
-- work_order.status: created, released, running, paused, completed, cancelled

CREATE TABLE production_line (
  id BIGINT PRIMARY KEY,
  line_code VARCHAR(32) NOT NULL UNIQUE,
  line_name VARCHAR(100) NOT NULL,
  workshop_name VARCHAR(100) NOT NULL,
  status SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE machine (
  id BIGINT PRIMARY KEY,
  line_id BIGINT NOT NULL,
  machine_code VARCHAR(32) NOT NULL UNIQUE,
  machine_name VARCHAR(100) NOT NULL,
  machine_type VARCHAR(50) NOT NULL,
  vendor_name VARCHAR(100),
  rated_power_kw DECIMAL(10,2),
  status SMALLINT NOT NULL DEFAULT 0,
  install_date DATE,
  CONSTRAINT fk_machine_line FOREIGN KEY (line_id) REFERENCES production_line(id)
);

CREATE TABLE sensor_point (
  id BIGINT PRIMARY KEY,
  machine_id BIGINT NOT NULL,
  point_code VARCHAR(64) NOT NULL UNIQUE,
  point_name VARCHAR(100) NOT NULL,
  metric_name VARCHAR(64) NOT NULL,
  data_type VARCHAR(20) NOT NULL,
  unit_name VARCHAR(20),
  high_limit DECIMAL(10,2),
  low_limit DECIMAL(10,2),
  is_key_process_point BOOLEAN NOT NULL DEFAULT FALSE,
  CONSTRAINT fk_sensor_machine FOREIGN KEY (machine_id) REFERENCES machine(id)
);

CREATE TABLE sensor_reading (
  id BIGINT PRIMARY KEY,
  sensor_id BIGINT NOT NULL,
  reading_value DECIMAL(12,3) NOT NULL,
  quality_code VARCHAR(20) NOT NULL DEFAULT 'GOOD',
  collected_at TIMESTAMP NOT NULL,
  CONSTRAINT fk_reading_sensor FOREIGN KEY (sensor_id) REFERENCES sensor_point(id)
);

CREATE TABLE alarm_event (
  id BIGINT PRIMARY KEY,
  machine_id BIGINT NOT NULL,
  alarm_code VARCHAR(32) NOT NULL,
  alarm_name VARCHAR(100) NOT NULL,
  severity SMALLINT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  triggered_at TIMESTAMP NOT NULL,
  cleared_at TIMESTAMP NULL,
  source_point_id BIGINT NULL,
  CONSTRAINT fk_alarm_machine FOREIGN KEY (machine_id) REFERENCES machine(id),
  CONSTRAINT fk_alarm_point FOREIGN KEY (source_point_id) REFERENCES sensor_point(id)
);

CREATE TABLE work_order (
  id BIGINT PRIMARY KEY,
  machine_id BIGINT NOT NULL,
  product_code VARCHAR(32) NOT NULL,
  product_name VARCHAR(100) NOT NULL,
  planned_quantity DECIMAL(12,2) NOT NULL,
  actual_quantity DECIMAL(12,2) NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'created',
  planned_start_time TIMESTAMP,
  planned_end_time TIMESTAMP,
  actual_start_time TIMESTAMP,
  actual_end_time TIMESTAMP,
  operator_name VARCHAR(100),
  CONSTRAINT fk_work_order_machine FOREIGN KEY (machine_id) REFERENCES machine(id)
);

CREATE TABLE maintenance_work_order (
  id BIGINT PRIMARY KEY,
  machine_id BIGINT NOT NULL,
  alarm_id BIGINT NULL,
  work_order_no VARCHAR(32) NOT NULL UNIQUE,
  issue_type VARCHAR(50) NOT NULL,
  priority VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'open',
  assignee_name VARCHAR(100),
  created_at TIMESTAMP NOT NULL,
  closed_at TIMESTAMP NULL,
  CONSTRAINT fk_maintenance_machine FOREIGN KEY (machine_id) REFERENCES machine(id),
  CONSTRAINT fk_maintenance_alarm FOREIGN KEY (alarm_id) REFERENCES alarm_event(id)
);
