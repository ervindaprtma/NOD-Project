# Stage 1: Build
FROM node:22-alpine AS builder

ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

WORKDIR /app

# Copy package.json first for better layer caching
COPY package.json ./
RUN npm install

COPY . .

RUN npm run build

# Stage 2: Production runtime
FROM node:22-alpine

WORKDIR /app

ENV NODE_ENV=production

# Copy package.json and install only production deps
COPY --from=builder /app/package.json ./
RUN npm install --omit=dev

# Copy built artifacts
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.mjs ./

EXPOSE 3000

CMD ["npm", "run", "start"]
