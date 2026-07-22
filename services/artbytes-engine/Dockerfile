###############################################################################
# Build stage
###############################################################################
FROM node:20-bookworm-slim AS build

WORKDIR /app

# Build deps for better-sqlite3
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --no-audit --no-fund

COPY tsconfig.json ./
COPY src ./src
RUN npm run build

###############################################################################
# Runtime stage
###############################################################################
FROM node:20-bookworm-slim AS runtime

WORKDIR /app
ENV NODE_ENV=production

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund \
  && apt-get purge -y python3 make g++ \
  && apt-get autoremove -y \
  && rm -rf /root/.npm

COPY --from=build /app/dist ./dist

# Persistent SQLite DB — chown so the non-root user can write
RUN mkdir -p /app/data && chown -R node:node /app /app/data
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/engine.db

USER node

CMD ["node", "dist/index.js"]
