-- RadioTiker vNext MySQL migration: track health checks
-- Apply with:
--   mysql --defaults-extra-file=~/.mysql-radio.cnf < infra/db/mysql/003_track_health.sql

CREATE TABLE IF NOT EXISTS track_health (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  track_uid CHAR(64) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  source_path TEXT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'warning',
  source_reachable TINYINT(1) NOT NULL DEFAULT 0,
  probe_ok TINYINT(1) NOT NULL DEFAULT 0,
  decode_ok TINYINT(1) NOT NULL DEFAULT 0,
  duration_sec DECIMAL(12,3) NULL,
  codec VARCHAR(64) NULL,
  error_reason TEXT NULL,
  details_json JSON NULL,
  checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_track_health_user_track_source (user_id, track_uid, source_path(255)),
  INDEX idx_track_health_user_status (user_id, status, checked_at),
  INDEX idx_track_health_track (track_uid),
  CONSTRAINT fk_track_health_track FOREIGN KEY (track_uid) REFERENCES tracks(track_uid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
