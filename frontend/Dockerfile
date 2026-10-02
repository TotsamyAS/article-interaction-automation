FROM oven/bun:1.4.2-alpine AS build
WORKDIR /app
COPY frontend/package.json ./
RUN bun install
COPY frontend/ ./
RUN bun run build

FROM oven/bun:1.4.2-alpine
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY frontend/server.ts frontend/config.json ./
EXPOSE 3033
CMD ["bun", "run", "server.ts"]
