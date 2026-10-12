import { BOUNDARY_SOURCE, PLACE_CATALOG } from './mockData';

const districts = PLACE_CATALOG.filter((place) => place.level === 'district');
const taluks = PLACE_CATALOG.filter((place) => place.level === 'taluk');

test('GeoBoundaries catalog includes all available Karnataka ADM2 and ADM3 polygons', () => {
  expect(districts).toHaveLength(BOUNDARY_SOURCE.coverage.districtBoundaries);
  expect(taluks).toHaveLength(BOUNDARY_SOURCE.coverage.adm3Boundaries);
  expect(districts).toHaveLength(30);
  expect(taluks).toHaveLength(229);
});

test('each ADM3 polygon belongs to one available district', () => {
  const districtIds = new Set(districts.map((district) => district.id));

  taluks.forEach((taluk) => {
    expect(districtIds.has(taluk.parentId)).toBe(true);
    expect(taluk.geometry.type).toMatch(/Polygon/);
  });
});

test('catalog metadata reports the missing current district and source vintages', () => {
  expect(BOUNDARY_SOURCE.coverage.missingDistrictName).toBe('Vijayanagara');
  expect(BOUNDARY_SOURCE.representedYears).toEqual({ districts: 2021, taluks: 2018 });
  expect(BOUNDARY_SOURCE.license).toContain('ODbL');
});
