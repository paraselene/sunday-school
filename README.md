# 主日學點名系統

為小型主日學設計的手機優先點名系統，使用 Django 5.2、SQLite、Gunicorn 及 ReportLab。

## 使用 Docker 執行

```sh
cp .env.example .env
# 編輯 .env 內的 SECRET_KEY、LOGIN_PASSWORD 及 ALLOWED_HOSTS
docker compose up -d --build
```

開啟 `http://localhost:8000/login/`，輸入共用的 `LOGIN_PASSWORD` 即可，毋須使用者名稱。應用程式資料儲存在名為 `sunday_school_data` 的 Docker volume，即使更換容器也會保留。

每週從星期一開始，該週第一次成功登入時會自動備份資料庫至同一個持久 volume 的 `/data/backups/`。若星期一沒有人登入，備份會在該週下一次成功登入時建立；同一週不會重複建立。請另行定期把 volume 備份複製到其他主機或儲存服務，以防主機故障。

## 本機開發

```sh
python3.13 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

使用 `python manage.py check`、`python manage.py makemigrations --check` 及 `python manage.py test` 執行檢查。

HTTPS 終止、網域／DNS 設定、定期 volume 備份及多執行個體部署屬於本應用程式以外的基礎設施工作。

PDF 使用本機 `fonts/NotoSansTC[wght].ttf` 的 Noto Sans TC（思源黑體繁中）。字型不會提交至 Git，也不會放進映像；Docker Compose 會將 `./fonts` 唯讀掛載到容器的 `/app/fonts`。

首次使用前下載字型：

```sh
mkdir -p fonts
curl -fL -o 'fonts/NotoSansTC[wght].ttf' 'https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf'
```
