-- RadioTiker vNext MySQL migration: track file instances + raw source tags
-- Apply with:
--   mysql --defaults-extra-file=~/.mysql-radio.cnf < infra/db/mysql/002_track_sources.sql

CREATE TABLE IF NOT EXISTS track_sources (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  track_uid CHAR(64) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  agent_id VARCHAR(128) NULL,
  source_path TEXT NOT NULL,
  file_size BIGINT NULL,
  mtime BIGINT NULL,
  checksum CHAR(64) NULL,
  duration_sec DECIMAL(12,3) NULL,
  codec VARCHAR(64) NULL,
  bitrate_kbps INT NULL,
  sample_rate INT NULL,
  channels INT NULL,
  source_rank INT NOT NULL DEFAULT 100,
  is_available TINYINT(1) NOT NULL DEFAULT 1,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_track_source_user_path (user_id, source_path(255)),
  INDEX idx_track_sources_track (track_uid),
  INDEX idx_track_sources_user_mtime (user_id, mtime),
  INDEX idx_track_sources_track_available (track_uid, is_available, source_rank),
  CONSTRAINT fk_track_sources_track FOREIGN KEY (track_uid) REFERENCES tracks(track_uid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS source_tags (
  track_source_id BIGINT PRIMARY KEY,
  title VARCHAR(512) NULL,
  artist VARCHAR(512) NULL,
  album VARCHAR(512) NULL,
  track_no VARCHAR(64) NULL,
  disc_no VARCHAR(64) NULL,
  year VARCHAR(64) NULL,
  genre VARCHAR(255) NULL,
  album_artist VARCHAR(512) NULL,
  composer VARCHAR(512) NULL,
  bpm DECIMAL(8,3) NULL,
  musical_key VARCHAR(64) NULL,
  raw_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_source_tags_source FOREIGN KEY (track_source_id) REFERENCES track_sources(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
