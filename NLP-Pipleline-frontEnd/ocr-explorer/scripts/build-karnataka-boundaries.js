const fs = require('fs');
const path = require('path');

const [adm1Path, adm2Path, adm3Path] = process.argv.slice(2);
if (!adm1Path || !adm2Path || !adm3Path) {
  throw new Error('Usage: node scripts/build-karnataka-boundaries.js <IND-ADM1.geojson> <IND-ADM2.geojson> <IND-ADM3.geojson>');
}

const readCollection = (filePath) => JSON.parse(fs.readFileSync(filePath, 'utf8'));
const [adm1, adm2, adm3] = [adm1Path, adm2Path, adm3Path].map(readCollection);

function coordinatesOf(coordinates, points = []) {
  if (typeof coordinates[0] === 'number') {
    points.push(coordinates);
  } else {
    coordinates.forEach((child) => coordinatesOf(child, points));
  }
  return points;
}

function geometryBounds(geometry) {
  const points = coordinatesOf(geometry.coordinates);
  const longitudes = points.map(([longitude]) => longitude);
  const latitudes = points.map(([, latitude]) => latitude);
  return [
    Math.min(...longitudes),
    Math.min(...latitudes),
    Math.max(...longitudes),
    Math.max(...latitudes),
  ];
}

function boundsCenter(feature) {
  const [west, south, east, north] = geometryBounds(feature.geometry);
  return [(west + east) / 2, (south + north) / 2];
}

function pointInRing([longitude, latitude], ring) {
  let inside = false;
  for (let current = 0, previous = ring.length - 1; current < ring.length; previous = current, current += 1) {
    const [currentLongitude, currentLatitude] = ring[current];
    const [previousLongitude, previousLatitude] = ring[previous];
    const crossesLatitude = (currentLatitude > latitude) !== (previousLatitude > latitude);
    const intersectionLongitude = ((previousLongitude - currentLongitude) * (latitude - currentLatitude))
      / (previousLatitude - currentLatitude) + currentLongitude;
    if (crossesLatitude && longitude < intersectionLongitude) inside = !inside;
  }
  return inside;
}

function pointInGeometry(point, geometry) {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
  return polygons.some(([outerRing, ...holes]) => (
    pointInRing(point, outerRing) && !holes.some((hole) => pointInRing(point, hole))
  ));
}

function pointInFeature(point, feature) {
  return pointInGeometry(point, feature.geometry);
}

const canonicalDistrictNames = {
  Bagalkot: 'Bagalkote',
  Bangalore: 'Bengaluru Urban',
  'Bangalore Rural': 'Bengaluru Rural',
  Belgaum: 'Belagavi',
  Bellary: 'Ballari',
  Bijapur: 'Vijayapura',
  Chikmagalur: 'Chikkamagaluru',
  Gulbarga: 'Kalaburagi',
  Mysore: 'Mysuru',
  Shimoga: 'Shivamogga',
  Tumkur: 'Tumakuru',
};

function slugify(value) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function toBoundaryFeature(sourceFeature, { level, parentId, id, name, sourceName }) {
  const bbox = geometryBounds(sourceFeature.geometry);
  return {
    type: 'Feature',
    id,
    bbox,
    properties: {
      placeId: id,
      parentId,
      level,
      name,
      sourceName,
      sourceFeatureId: sourceFeature.properties.shapeID,
      boundaryVintage: level === 'district' ? 2021 : level === 'taluk' ? 2018 : 2021,
    },
    geometry: sourceFeature.geometry,
  };
}

const stateSource = adm1.features.find((feature) => feature.properties.shapeISO === 'IN-KA');
if (!stateSource) throw new Error('ADM1 source does not contain Karnataka (IN-KA).');

const districtSources = adm2.features.filter((feature) => pointInFeature(boundsCenter(feature), stateSource));
if (districtSources.length !== 30) {
  throw new Error(`Expected 30 Karnataka districts in the selected ADM2 vintage; found ${districtSources.length}.`);
}

const districts = districtSources.map((sourceFeature) => {
  const sourceName = sourceFeature.properties.shapeName;
  const name = canonicalDistrictNames[sourceName] || sourceName;
  return toBoundaryFeature(sourceFeature, {
    level: 'district',
    parentId: 'KA',
    id: `KA-${slugify(name)}`,
    name,
    sourceName,
  });
});
const districtById = new Map(districts.map((district) => [district.id, district]));

const stateFeature = toBoundaryFeature(stateSource, {
  level: 'state',
  parentId: null,
  id: 'KA',
  name: 'Karnataka',
  sourceName: stateSource.properties.shapeName,
});

const talukSources = adm3.features.filter((feature) => pointInFeature(boundsCenter(feature), stateSource));
if (talukSources.length !== 229) {
  throw new Error(`Expected 229 Karnataka ADM3 features in the selected ADM3 vintage; found ${talukSources.length}.`);
}

const taluks = talukSources.map((sourceFeature) => {
  const parent = districts.find((district) => pointInFeature(boundsCenter(sourceFeature), district));
  if (!parent) {
    throw new Error(`Could not assign ADM3 feature "${sourceFeature.properties.shapeName}" to an ADM2 district.`);
  }
  const sourceName = sourceFeature.properties.shapeName;
  const id = `${parent.id}-T-${slugify(sourceName)}-${sourceFeature.properties.shapeID}`;
  if (!districtById.has(parent.id)) throw new Error(`Unknown district parent ${parent.id}.`);
  return toBoundaryFeature(sourceFeature, {
    level: 'taluk',
    parentId: parent.id,
    id,
    name: sourceName,
    sourceName,
  });
});

const output = {
  type: 'FeatureCollection',
  metadata: {
    source: 'geoBoundaries',
    sourceUrl: 'https://www.geoboundaries.org/',
    license: 'Open Data Commons Open Database License (ODbL) 1.0',
    attribution: 'geoBoundaries (www.geoboundaries.org), ODbL 1.0',
    representedYears: { districts: 2021, taluks: 2018 },
    coverage: {
      districtBoundaries: districts.length,
      currentDistrictNames: 31,
      adm3Boundaries: taluks.length,
      missingDistrictName: 'Vijayanagara',
    },
    talukParentMethod: 'ADM3 bounding-box center assigned to containing ADM2 polygon',
  },
  features: [stateFeature, ...districts, ...taluks],
};

const outputPath = path.join(__dirname, '../src/atlas/karnatakaBoundaries.json');
fs.writeFileSync(outputPath, `${JSON.stringify(output)}\n`);
console.log(`Wrote ${output.features.length} boundaries to ${outputPath}`);
