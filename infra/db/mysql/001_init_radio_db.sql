-- RadioTiker vNext MySQL baseline schema (RDS MySQL 8.x)
-- Apply with:
--   mysql --defaults-extra-file=~/.mysql-radio.cnf < infra/db/mysql/001_init_radio_db.sql

CREATE TABLE IF NOT EXISTS tracks (
  track_uid CHAR(64) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  source_path TEXT NULL,
  source_hash CHAR(64) NULL,
  title VARCHAR(512) NULL,
  artist VARCHAR(512) NULL,
  album VARCHAR(512) NULL,
  year INT NULL,
  genre VARCHAR(255) NULL,
  artwork_url TEXT NULL,
  artist_image_urls JSON NULL,
  artist_bio MEDIUMTEXT NULL,
  album_bio MEDIUMTEXT NULL,
  provider_ids JSON NULL,
  canonical_json JSON NULL,
  override_json JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_tracks_user (user_id),
  INDEX idx_tracks_artist (artist(191)),
  INDEX idx_tracks_album (album(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS provider_snapshots (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  track_uid CHAR(64) NOT NULL,
  provider VARCHAR(64) NOT NULL,
  provider_ref VARCHAR(255) NULL,
  score DECIMAL(6,4) NULL,
  payload_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_ps_track (track_uid),
  INDEX idx_ps_provider (provider),
  CONSTRAINT fk_ps_track FOREIGN KEY (track_uid) REFERENCES tracks(track_uid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS metadata_overrides (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  track_uid CHAR(64) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  override_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_override_track_user (track_uid, user_id),
  INDEX idx_override_user (user_id),
  CONSTRAINT fk_override_track FOREIGN KEY (track_uid) REFERENCES tracks(track_uid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS media_cache (
  cache_key CHAR(64) PRIMARY KEY,
  source_url TEXT NOT NULL,
  mime_type VARCHAR(128) NULL,
  size_bytes BIGINT NULL,
  sha256 CHAR(64) NULL,
  storage_kind ENUM('remote', 's3') NOT NULL DEFAULT 'remote',
  storage_url TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
