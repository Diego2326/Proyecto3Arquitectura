FROM node:24-slim

ENV NODE_ENV=production
WORKDIR /app

COPY package.json ./
COPY server.js index.html ./

EXPOSE 8080
CMD ["npm", "start"]
