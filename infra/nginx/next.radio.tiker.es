server {
    listen 80;
    server_name next.radio.tiker.es;

    # Allow Certbot HTTP-01 challenges
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    # Redirect all HTTP to HTTPS once certs are installed
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name next.radio.tiker.es;

    # Cert paths (set by certbot)
    ssl_certificate     /etc/letsencrypt/live/next.radio.tiker.es/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/next.radio.tiker.es/privkey.pem;

    # Basic hardening
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer-when-downgrade always;

    # Root landing page with agent download links.
    location = / {
        root /var/www/next.radio.tiker.es/html;
        try_files /index.html =404;
    }

    # Streamer API (vnext)
    location /streamer/api/ {
        proxy_pass         http://127.0.0.1:8091/api/;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection "";
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
    }

    # Optional: simple health without hitting app
    location = /streamer/api/health {
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }
}
