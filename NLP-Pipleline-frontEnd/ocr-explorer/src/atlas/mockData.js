// Mock atlas data for the Karnataka Heat Atlas.
// This module is intentionally API-shaped so the eventual backend can swap in with no UI churn.

import boundaryData from './karnatakaBoundaries.json';

export const YEARS = [2000, 2005, 2010, 2015, 2020, 2025];

export const LAYER_META = [
  {
    id: 'lst',
    label: 'Land Surface Temperature',
    unit: '°C',
    years: YEARS,
    source: 'MODIS LST / GEE',
    sensor: 'MODIS Terra/Aqua',
    resolutionM: 1000,
    season: 'summer',
    description: 'Mean daytime surface temperature for the selected period.',
    citation: 'MODIS Land Surface Temperature product (collection-based)',
    colorRamp: 'inferno',
    domain: [20, 42],
  },
  {
    id: 'ndvi',
    label: 'NDVI',
    unit: 'index',
    years: YEARS,
    source: 'MODIS NDVI / GEE',
    sensor: 'MODIS',
    resolutionM: 250,
    season: 'monsoon',
    description: 'Vegetation vigour and canopy density proxy.',
    citation: 'MODIS Vegetation Indices product',
    colorRamp: 'YlGn',
    domain: [0, 1],
  },
  {
    id: 'ndbi',
    label: 'NDBI',
    unit: 'index',
    years: YEARS,
    source: 'Landsat / GEE',
    sensor: 'Landsat 7/8/9',
    resolutionM: 30,
    description: 'Built-up index used to fingerprint urban and impervious surfaces.',
    citation: 'Landsat normalized difference built-up index composite',
    colorRamp: 'PuRd',
    domain: [-0.4, 0.8],
  },
  {
    id: 'lulc',
    label: 'Land use / land cover',
    unit: '%',
    years: YEARS,
    source: 'ESA WorldCover / local classification',
    sensor: 'Sentinel / ESA',
    resolutionM: 10,
    description: 'Land-cover composition across built-up, vegetation, water and other classes.',
    citation: 'WorldCover and local land-use classification stack',
    colorRamp: 'viridis',
    domain: [0, 100],
  },
  {
    id: 'events',
    label: 'Historical events',
    unit: 'count',
    years: YEARS,
    source: 'Archival NLP evidence',
    sensor: 'document extraction',
    resolutionM: 0,
    description: 'Evidence-weighted event density for the selected place and year.',
    citation: 'Archival and policy document extraction pipeline',
    colorRamp: 'cividis',
    domain: [0, 12],
  },
];

export const PLACE_CATALOG = boundaryData.features.map((feature) => {
  const [west, south, east, north] = feature.bbox;
  return {
    id: feature.properties.placeId,
    name: feature.properties.name,
    level: feature.properties.level,
    parentId: feature.properties.parentId,
    bbox: [west, south, east, north],
    centroid: [(west + east) / 2, (south + north) / 2],
    geometry: feature.geometry,
  };
});

export const BOUNDARY_SOURCE = boundaryData.metadata;
export const PLACE_STATS = [];

export const DOCUMENT_RECORDS = [
  {
    id: 'doc-001',
    title: 'Temperature risk in Bengaluru municipal wards',
    year: 2018,
    docType: 'Policy brief',
    source: 'Urban Development Department',
    url: 'https://example.org/docs/bengaluru-risk',
    placeIds: ['KA-BLR-U'],
    themes: ['Heat', 'Urbanisation', 'Policy'],
    entities: [
      { text: 'Bengaluru', type: 'PLACE' },
      { text: 'Urban Development Department', type: 'ORG' },
      { text: '2018', type: 'DATE' },
    ],
    snippet: 'Heat exposure in the city core is rising faster than surrounding wards, indicating a widening urban heat island.',
    isMock: true,
  },
  {
    id: 'doc-002',
    title: 'Canopy loss and local climate stress',
    year: 2014,
    docType: 'Research note',
    source: 'State ecology unit',
    url: 'https://example.org/docs/canopy-loss',
    placeIds: ['KA-BLR', 'KA-MYS'],
    themes: ['Heat', 'Water', 'Urbanisation'],
    entities: [
      { text: 'Bengaluru', type: 'PLACE' },
      { text: 'Mysuru', type: 'PLACE' },
      { text: 'water stress', type: 'ORG' },
    ],
    snippet: 'Canopy loss coupled with water scarcity increased exposure during summer heat spikes in city-adjacent districts.',
    isMock: true,
  },
  {
    id: 'doc-003',
    title: 'Land records and settlement pressure',
    year: 2007,
    docType: 'Archive extract',
    source: 'District gazetteer',
    url: 'https://example.org/docs/land-records',
    placeIds: ['KA-DVG'],
    themes: ['Land tenure', 'Policy'],
    entities: [
      { text: 'Davanagere', type: 'PLACE' },
      { text: 'land records', type: 'ORG' },
      { text: '2007', type: 'DATE' },
    ],
    snippet: 'Agrarian settlement pressures and encroachment patterns are aligned with rising built-up intensity in the taluk belt.',
    isMock: true,
  },
];

export const EVENT_RECORDS = [
  {
    id: 'evt-001',
    title: 'Heat emergency alert for urban wards',
    year: 2016,
    dateText: 'May 2016',
    placeIds: ['KA-BLR-U'],
    themes: ['Heat', 'Policy'],
    summary: 'Municipal response flagged elevated night-time temperatures in densely built residential zones.',
    documentIds: ['doc-001'],
    location: [77.61, 12.97],
  },
  {
    id: 'evt-002',
    title: 'Lake restoration push',
    year: 2012,
    dateText: 'August 2012',
    placeIds: ['KA-BLR'],
    themes: ['Water', 'Urbanisation'],
    summary: 'A lake restoration campaign targeted heat mitigation and stormwater retention near central Bengaluru.',
    documentIds: ['doc-002'],
    location: [77.58, 12.96],
  },
  {
    id: 'evt-003',
    title: 'Agri-land conversion review',
    year: 2009,
    dateText: 'October 2009',
    placeIds: ['KA-DVG'],
    themes: ['Land tenure', 'Urbanisation'],
    summary: 'A policy review documented conversion pressure on peri-urban agricultural land.',
    documentIds: ['doc-003'],
    location: [75.92, 14.47],
  },
];

export const DEFAULT_ATLAS_FILTERS = {
  layer: 'lst',
  year: 2020,
  level: 'district',
  statistic: 'mean',
  themes: [],
  selectedPlaceId: 'KA-BENGALURU-URBAN',
};

export const ATLAS_MOCK_FLAG = 'mock-data';

export const atlasApiContract = {
  layers: LAYER_META,
  places: PLACE_CATALOG,
  stats: PLACE_STATS,
  documents: DOCUMENT_RECORDS,
  events: EVENT_RECORDS,
};

export default atlasApiContract;
