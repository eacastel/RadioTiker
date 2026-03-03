# MySQL RDS Operations (Hetzner -> AWS)

This runbook covers setup and operations for `radio_db` on AWS RDS MySQL.

## 1) Client Auth File

Create `~/.mysql-radio.cnf` on the Hetzner host:

```ini
[client]
host=endossmp-database-1.cvdwqbgtvzwi.us-west-2.rds.amazonaws.com
port=3306
user=radio_app
password=REPLACE_WITH_PASSWORD
ssl-mode=REQUIRED
database=radio_db
```

```bash
chmod 600 ~/.mysql-radio.cnf
mysql --defaults-extra-file=~/.mysql-radio.cnf -e "SELECT DATABASE(), CURRENT_USER();"
```

## 2) Network Security Group

For RDS inbound rules:
1. Allow `TCP 3306` from Hetzner public IP as `/32` only.
2. Do not allow `0.0.0.0/0`.
3. Add temporary admin IP `/32` only when needed.

## 3) Apply Schema

```bash
cd ~/RadioTiker-vnext
infra/scripts/radio_db_apply.sh ~/.mysql-radio.cnf infra/db/mysql/001_init_radio_db.sql
```

## 3.1) Enable API -> MySQL Dual Write

The API reads DB config from `RADIO_DB_DSN` (or `DATABASE_URL`).
Set it in `/etc/default/rt-streamer-vnext` because systemd service already loads this file.

```bash
sudo tee -a /etc/default/rt-streamer-vnext >/dev/null <<'EOF'
RADIO_DB_DSN=mysql://radio_app:REPLACE_WITH_PASSWORD@endossmp-database-1.cvdwqbgtvzwi.us-west-2.rds.amazonaws.com:3306/radio_db
EOF
```

Restart service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart rt-streamer-vnext.service
sudo systemctl status rt-streamer-vnext.service --no-pager
```

## 4) Backup and Restore

Backup:

```bash
cd ~/RadioTiker-vnext
infra/scripts/radio_db_backup.sh ~/.mysql-radio.cnf ~/backups/radio_db
```

Note: default backup is least-privilege friendly (tables + triggers, no events/routines).

Restore:

```bash
cd ~/RadioTiker-vnext
infra/scripts/radio_db_restore.sh ~/.mysql-radio.cnf ~/backups/radio_db/radio_db_YYYYMMDDTHHMMSSZ.sql.gz
```

## 5) Daily Backup Cron

Run once:

```bash
crontab -e
```

Add:

```cron
15 3 * * * cd /home/eacastel/RadioTiker-vnext && /home/eacastel/RadioTiker-vnext/infra/scripts/radio_db_backup.sh /home/eacastel/.mysql-radio.cnf /home/eacastel/backups/radio_db >> /home/eacastel/backups/radio_db/backup.log 2>&1
```

Optional retention (keep last 14 days):

```cron
45 3 * * * find /home/eacastel/backups/radio_db -name "radio_db_*.sql.gz" -mtime +14 -delete
```

## 6) Changing DB Port Safely

RDS port changes are disruptive and require coordinated updates.

Checklist:
1. Change port in RDS instance settings.
2. Update security group inbound rule to new port.
3. Update `~/.mysql-radio.cnf` port.
4. Update app `DATABASE_URL` with new port.
5. Restart app service.
6. Validate:
   - `mysql --defaults-extra-file=~/.mysql-radio.cnf -e "SELECT 1;"`
   - app health endpoint.

## 7) Recommended Access Model

Use:
1. `radio_app` for application runtime (least privilege).
2. separate admin user for DDL/grants only.
3. no shared credentials between services.
