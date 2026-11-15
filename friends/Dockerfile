FROM python:3.12-slim
WORKDIR /app
COPY friends/ /app/friends/
COPY chuddy/__init__.py chuddy/leaderboard.py /app/chuddy/
RUN pip install --no-cache-dir tzdata==2026.5
RUN useradd --create-home chuddy && mkdir /data && chown chuddy:chuddy /data
USER chuddy
ENV CHUDDY_FRIENDS_DB=/data/friends.sqlite3
VOLUME ["/data"]
EXPOSE 8770
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8770/health',timeout=2)"
CMD ["python", "-m", "friends.service", "--host", "0.0.0.0"]
