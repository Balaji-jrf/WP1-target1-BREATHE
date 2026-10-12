const fs = require('fs');
const path = require('path');

const workerSource = require.resolve('maplibre-gl/dist/maplibre-gl-worker.mjs');
const workerDestination = path.resolve(__dirname, '../public/maplibre-gl-worker.js');

fs.copyFileSync(workerSource, workerDestination);
console.log('Copied MapLibre worker to public/maplibre-gl-worker.js');
