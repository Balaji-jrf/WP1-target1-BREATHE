import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BOUNDARY_SOURCE,
  DEFAULT_ATLAS_FILTERS,
  DOCUMENT_RECORDS,
  EVENT_RECORDS,
  LAYER_META,
  PLACE_CATALOG,
  PLACE_STATS,
  YEARS,
} from './index';
import AtlasMap from './AtlasMap';

const allYears = YEARS;
const allStats = ['mean', 'min', 'max', 'trend'];
const placeById = new Map(PLACE_CATALOG.map((place) => [place.id, place]));

const formatValue = (layerId, value, stat, unit) => {
  if (stat === 'trend') return `${Number(value).toFixed(2)} ${unit}/yr`;
  if (layerId === 'ndvi') return `${Number(value).toFixed(3)}`;
  if (layerId === 'lulc') return `${Number(value).toFixed(1)}%`;
  if (layerId === 'events') return `${Number(value).toFixed(0)} events`;
  return `${Number(value).toFixed(1)} ${unit}`;
};

function readUrlFilters() {
  const params = new URLSearchParams(window.location.search);
  const layer = params.get('layer');
  const year = Number(params.get('year'));
  const place = params.get('place');
  const statistic = params.get('stat');
  return {
    layer: LAYER_META.some((entry) => entry.id === layer) ? layer : DEFAULT_ATLAS_FILTERS.layer,
    year: allYears.includes(year) ? year : DEFAULT_ATLAS_FILTERS.year,
    selectedPlaceId: PLACE_CATALOG.some((entry) => entry.id === place) ? place : DEFAULT_ATLAS_FILTERS.selectedPlaceId,
    statistic: allStats.includes(statistic) ? statistic : DEFAULT_ATLAS_FILTERS.statistic,
  };
}

function writeUrlFilters(filters, replace = false) {
  const params = new URLSearchParams(window.location.search);
  params.set('view', 'atlas');
  params.set('layer', filters.layer);
  params.set('year', String(filters.year));
  params.set('level', filters.level);
  params.set('place', filters.selectedPlaceId);
  params.set('stat', filters.statistic);
  const nextUrl = `${window.location.pathname}?${params.toString()}${window.location.hash}`;
  window.history[replace ? 'replaceState' : 'pushState'](null, '', nextUrl);
}

function ancestorAtLevel(place, level) {
  let current = place;
  while (current && current.level !== level) {
    current = placeById.get(current.parentId);
  }
  return current || null;
}

export default function AtlasPage() {
  const [urlFilters, setUrlFilters] = useState(readUrlFilters);
  const [playing, setPlaying] = useState(false);
  const [compareMode, setCompareMode] = useState(false);
  const [compareYear, setCompareYear] = useState(2010);
  const [basemap, setBasemap] = useState('light');
  const [mapError, setMapError] = useState('');
  const [shareMessage, setShareMessage] = useState('');

  const activeLayer = urlFilters.layer;
  const year = urlFilters.year;
  const stat = urlFilters.statistic;
  const selectedPlace = PLACE_CATALOG.find((place) => place.id === urlFilters.selectedPlaceId)
    || PLACE_CATALOG.find((place) => place.id === DEFAULT_ATLAS_FILTERS.selectedPlaceId);
  const districts = PLACE_CATALOG.filter((place) => place.level === 'district' && place.parentId === 'KA');
  const selectedDistrict = ancestorAtLevel(selectedPlace, 'district') || districts[0];
  const taluks = PLACE_CATALOG.filter((place) => place.level === 'taluk' && place.parentId === selectedDistrict?.id);
  const selectedTaluk = ancestorAtLevel(selectedPlace, 'taluk');
  const children = PLACE_CATALOG.filter((place) => place.parentId === selectedPlace.id);
  const mapPlaces = children.length
    ? children
    : PLACE_CATALOG.filter((place) => place.parentId === selectedPlace.parentId && place.id !== selectedPlace.id);

  const activeMeta = useMemo(
    () => LAYER_META.find((layer) => layer.id === activeLayer) || LAYER_META[0],
    [activeLayer]
  );

  const placeStats = useMemo(
    () => PLACE_STATS.find((entry) => entry.placeId === selectedPlace.id && entry.layer === activeLayer && entry.year === year),
    [activeLayer, selectedPlace.id, year]
  );

  const currentValue = placeStats;
  const metricValue = currentValue?.[stat] ?? currentValue?.mean;
  const chartValues = allYears.map((chartYear) => (
    PLACE_STATS.find((entry) => entry.placeId === selectedPlace.id && entry.layer === activeLayer && entry.year === chartYear)?.[stat]
  ));
  const chartNumbers = chartValues.filter(Number.isFinite);
  const hasChartData = chartNumbers.length > 0;
  const maxChartValue = Math.max(...chartNumbers, 0.01);
  const hasEnvironmentalData = PLACE_STATS.some((entry) => entry.layer === activeLayer && entry.year === year);
  const selectedDocuments = DOCUMENT_RECORDS.filter((document) => document.placeIds.includes(selectedPlace.id));
  const selectedEvents = EVENT_RECORDS.filter((event) => event.placeIds.includes(selectedPlace.id));
  const eventsAtYear = (targetYear) => selectedEvents.filter((event) => event.year <= targetYear);

  const updateFilters = useCallback((patch, replace = false) => {
    const nextPlaceId = patch.selectedPlaceId || urlFilters.selectedPlaceId;
    const next = {
      ...urlFilters,
      ...patch,
      level: PLACE_CATALOG.find((place) => place.id === nextPlaceId)?.level || selectedPlace.level,
    };
    setUrlFilters(next);
    writeUrlFilters(next, replace);
  }, [selectedPlace.level, urlFilters]);

  useEffect(() => {
    const handlePopState = () => setUrlFilters(readUrlFilters());
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    writeUrlFilters({ ...urlFilters, level: selectedPlace.level }, true);
  }, [selectedPlace.level, urlFilters]);

  useEffect(() => {
    if (!playing) return undefined;
    const timer = window.setInterval(() => {
      const currentIndex = allYears.indexOf(year);
      const nextYear = allYears[(currentIndex + 1) % allYears.length];
      updateFilters({ year: nextYear }, true);
    }, 1400);
    return () => window.clearInterval(timer);
  }, [playing, year, updateFilters]);

  useEffect(() => {
    if (!compareMode || compareYear !== year) return;
    const currentIndex = allYears.indexOf(year);
    setCompareYear(allYears[currentIndex > 0 ? currentIndex - 1 : currentIndex + 1]);
  }, [compareMode, compareYear, year]);

  const ancestors = [];
  let ancestor = selectedPlace;
  while (ancestor) {
    ancestors.unshift(ancestor);
    ancestor = placeById.get(ancestor.parentId);
  }

  const statForPlace = (placeId, dataYear = year) => {
    const entry = PLACE_STATS.find((record) => record.placeId === placeId && record.layer === activeLayer && record.year === dataYear);
    return entry?.[stat] ?? entry?.mean ?? null;
  };

  const summaryCards = [
    { label: 'Selected layer', value: activeMeta.label },
    { label: 'Current year', value: String(year) },
    { label: 'Metric', value: stat.toUpperCase() },
    { label: 'Study area', value: selectedPlace.name },
  ];

  const selectPlace = (placeId) => updateFilters({ selectedPlaceId: placeId });
  const selectDistrict = (districtId) => updateFilters({ selectedPlaceId: districtId });
  const selectTaluk = (talukId) => updateFilters({ selectedPlaceId: talukId || selectedDistrict.id });
  const resetAtlas = () => {
    setPlaying(false);
    setCompareMode(false);
    setCompareYear(2010);
    setBasemap('light');
    updateFilters({
      layer: DEFAULT_ATLAS_FILTERS.layer,
      year: DEFAULT_ATLAS_FILTERS.year,
      selectedPlaceId: DEFAULT_ATLAS_FILTERS.selectedPlaceId,
      statistic: DEFAULT_ATLAS_FILTERS.statistic,
    });
  };

  const copyShareLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setShareMessage('Share link copied');
    } catch {
      setShareMessage('Clipboard unavailable — copy the URL from your address bar.');
    }
  };

  return (
    <section className="atlas-page">
      <div className="atlas-hero">
        <div>
          <p className="section-kicker">Spatial atlas · WP1</p>
          <h2>Karnataka Heat Atlas</h2>
        </div>
        <div className="atlas-hero-actions">
          <button className="ghost-btn" type="button" onClick={resetAtlas}>Reset view</button>
          <button
            className="primary-btn"
            type="button"
            onClick={copyShareLink}
            title="Copy this view's shareable URL"
          >
            Copy share link
          </button>
        </div>
      </div>
      {shareMessage && <p className="share-message" role="status">{shareMessage}</p>}
      <div className="atlas-data-notice" role="note">
        <strong>Boundary coverage &amp; data status</strong>
        <p>
          This GeoBoundaries snapshot contains {BOUNDARY_SOURCE.coverage.districtBoundaries} of
          {' '}{BOUNDARY_SOURCE.coverage.currentDistrictNames} current district names;
          {' '}{BOUNDARY_SOURCE.coverage.missingDistrictName} is absent. It includes
          {' '}{BOUNDARY_SOURCE.coverage.adm3Boundaries} ADM3 sub-district polygons (2018 vintage),
          assigned to 2021 ADM2 districts by polygon containment. These are not a verified current taluk roster.
        </p>
        <p>
          Boundaries: <a href={BOUNDARY_SOURCE.sourceUrl} target="_blank" rel="noreferrer">geoBoundaries</a>,
          {' '}ODbL 1.0 (ADM2 represented year 2021; ADM3 represented year 2018).
          LST, NDVI, NDBI, land-cover and event measurements are not loaded; map colors and charts
          therefore show boundaries only, not observed environmental values.
        </p>
      </div>

      <div className="atlas-toolbar">
        <div className="chip-group" aria-label="Layer filters">
          {LAYER_META.map((layer) => (
            <button
              key={layer.id}
              type="button"
              className={layer.id === activeLayer ? 'chip active' : 'chip'}
              aria-pressed={layer.id === activeLayer}
              onClick={() => updateFilters({ layer: layer.id })}
            >
              {layer.label}
            </button>
          ))}
        </div>

        <div className="range-wrap">
          <label htmlFor="atlas-year">Year</label>
          <input
            id="atlas-year"
            type="range"
            min={allYears[0]}
            max={allYears[allYears.length - 1]}
            step={5}
            value={year}
            onChange={(event) => updateFilters({ year: Number(event.target.value) }, true)}
          />
          <span>{year}</span>
          <button type="button" className={playing ? 'play-button active' : 'play-button'} onClick={() => setPlaying((current) => !current)} aria-label={playing ? 'Pause year animation' : 'Play year animation'} aria-pressed={playing}>
            {playing ? 'Ⅱ' : '▶'}
          </button>
        </div>
        <button type="button" className={compareMode ? 'compare-toggle active' : 'compare-toggle'} onClick={() => setCompareMode((current) => !current)} aria-pressed={compareMode}>
          {compareMode ? 'Close compare' : 'Compare years'}
        </button>
      </div>

      <div className="atlas-grid">
        <div className="atlas-map-panel">
          <div className="panel-header">
            <div>
              <p className="section-kicker">GeoBoundaries · ADM2 2021 / ADM3 2018</p>
              <h3>{selectedPlace.name}</h3>
              <nav className="atlas-breadcrumbs" aria-label="Place hierarchy">
                {ancestors.map((place, index) => (
                  <span key={place.id}>
                    {index > 0 && <b>/</b>}
                    <button type="button" onClick={() => selectPlace(place.id)} aria-current={place.id === selectedPlace.id ? 'page' : undefined}>
                      {place.name}
                    </button>
                  </span>
                ))}
              </nav>
            </div>
            <div className="place-selectors" aria-label="Karnataka administrative filters">
              <label className="place-search">
                <span>State</span>
                <select value="KA" onChange={() => updateFilters({ selectedPlaceId: 'KA' })}>
                  <option value="KA">Karnataka</option>
                </select>
              </label>
              <label className="place-search">
                <span>District · ADM2</span>
                <select value={selectedDistrict.id} onChange={(event) => selectDistrict(event.target.value)}>
                  {districts.map((district) => <option value={district.id} key={district.id}>{district.name}</option>)}
                </select>
              </label>
              <label className="place-search">
                <span>Sub-district / taluk · ADM3</span>
                <select
                  value={selectedTaluk?.parentId === selectedDistrict.id ? selectedTaluk.id : ''}
                  onChange={(event) => selectTaluk(event.target.value)}
                  disabled={!taluks.length}
                >
                  <option value="">{taluks.length ? 'Select a taluk' : 'No ADM3 boundaries available'}</option>
                  {taluks.map((taluk) => <option value={taluk.id} key={taluk.id}>{taluk.name}</option>)}
                </select>
              </label>
            </div>
          </div>

          <div className={compareMode ? 'compare-map-grid' : 'single-map'}>
            <div className="map-column">
              {compareMode && <p className="map-year-label">Current · {year}</p>}
              <AtlasMap
                places={mapPlaces}
                getValue={(placeId) => statForPlace(placeId)}
                selectedPlace={selectedPlace}
                activeLayer={activeLayer}
                activeMeta={activeMeta}
                events={eventsAtYear(year)}
                basemap={basemap}
                hasData={hasEnvironmentalData}
                onBasemapChange={setBasemap}
                onPlaceSelect={selectPlace}
                onMapError={(error) => setMapError(error?.message || 'Map tiles are unavailable.')}
              />
            </div>
            {compareMode && (
              <div className="map-column">
                <label className="map-year-label compare-year-select">
                  Compare with
                  <select value={compareYear} onChange={(event) => setCompareYear(Number(event.target.value))}>
                    {allYears.filter((value) => value !== year).map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <AtlasMap
                  places={mapPlaces}
                  getValue={(placeId) => statForPlace(placeId, compareYear)}
                  selectedPlace={selectedPlace}
                  activeLayer={activeLayer}
                  activeMeta={activeMeta}
                  events={eventsAtYear(compareYear)}
                  basemap={basemap}
                  hasData={PLACE_STATS.some((entry) => entry.layer === activeLayer && entry.year === compareYear)}
                  onBasemapChange={setBasemap}
                  onPlaceSelect={selectPlace}
                  compact
                  onMapError={(error) => setMapError(error?.message || 'Map tiles are unavailable.')}
                />
              </div>
            )}
          </div>

          {mapError && <p className="map-error" role="status">{mapError}</p>}
          {children.length > 0 && (
            <div className="drilldown-row">
              <span>Explore {children[0].level}s</span>
              {children.map((place) => (
                <button key={place.id} type="button" onClick={() => selectPlace(place.id)}>
                  {place.name}<span>↗</span>
                </button>
              ))}
            </div>
          )}

          <div className="legend-card">
            <div className="legend-topline">
              <span>{hasEnvironmentalData ? activeMeta.label : 'Administrative boundaries only'}</span>
              <strong>{hasEnvironmentalData ? activeMeta.unit : 'No data'}</strong>
            </div>
            {hasEnvironmentalData
              ? <><div className={`legend-bar legend-${activeLayer}`} /><div className="legend-scale"><span>{activeMeta.domain[0]}</span><span>{activeMeta.domain[1]}</span></div></>
              : <p className="data-unavailable">Verified {activeMeta.label} observations have not been loaded.</p>}
          </div>
        </div>

        <aside className="atlas-side-panel">
          <div className="metric-card metric-featured">
            <p className="section-kicker">Current signal</p>
            <h3>{metricValue === undefined ? 'Not loaded' : formatValue(activeLayer, metricValue, stat, activeMeta.unit)}</h3>
            {currentValue && <div className="mini-row">
              <span>Min {formatValue(activeLayer, currentValue.min ?? currentValue.mean, 'min', activeMeta.unit)}</span>
              <span>Max {formatValue(activeLayer, currentValue.max ?? currentValue.mean, 'max', activeMeta.unit)}</span>
            </div>}
          </div>

          <div className="metric-card">
            <p className="section-kicker">Annual change</p>
            <h4>{currentValue ? formatValue(activeLayer, currentValue.trend ?? 0, 'trend', activeMeta.unit) : 'Not available'}</h4>
            <small>{currentValue ? 'Linear trend for the selected time series' : 'No verified measurements for this layer/year.'}</small>
          </div>

          <div className="metric-card compact-list">
            <p className="section-kicker">Nearby places · {year}</p>
            {mapPlaces.length > 0 ? mapPlaces.map((place) => (
              <button key={place.id} type="button" className="place-row place-row-button" onClick={() => selectPlace(place.id)}>
                <span className="dot" />
                <span>{place.name}</span>
                <strong>{Number.isFinite(statForPlace(place.id)) ? formatValue(activeLayer, statForPlace(place.id), stat, activeMeta.unit) : '—'}</strong>
              </button>
            )) : <small>No neighboring boundary features are available at this level.</small>}
          </div>
        </aside>
      </div>

      <div className="atlas-summaries">
        {summaryCards.map((card) => (
          <div key={card.label} className="summary-card">
            <small>{card.label}</small>
            <strong>{card.value}</strong>
          </div>
        ))}
      </div>

      <div className="atlas-lower-grid">
        <div className="info-card chart-card">
          <div className="panel-header compact">
            <div>
              <p className="section-kicker">Timeseries</p>
              <h3>{activeMeta.label}</h3>
            </div>
          </div>

          {hasChartData ? (
            <div className="sparkline" aria-label={`${activeMeta.label} values from 2000 to 2025`}>
              {chartValues.map((point, index) => Number.isFinite(point) && (
                <span
                  key={allYears[index]}
                  className={allYears[index] === year ? 'sparkline-current' : ''}
                  style={{ height: `${Math.max(10, (point / maxChartValue) * 100)}%` }}
                  title={`${allYears[index]}: ${formatValue(activeLayer, point, stat, activeMeta.unit)}`}
                />
              ))}
            </div>
          ) : <p className="data-unavailable">No verified {activeMeta.label} time series has been loaded.</p>}
          {hasChartData && <div className="sparkline-labels">
            {allYears.map((value) => (
              <span key={value}>{value}</span>
            ))}
          </div>}
        </div>

        <div className="info-card">
          <div className="panel-header compact">
            <div>
              <p className="section-kicker">Archival evidence · mock records</p>
              <h3>Documents & events</h3>
            </div>
          </div>
          <div className="atlas-evidence-list">
            {selectedDocuments.map((document) => (
              <article key={document.id}>
                <span>{document.year || 'Undated'} · {document.docType}</span>
                <strong>{document.title}</strong>
                <p>{document.snippet}</p>
                <div className="evidence-tags">{document.themes.map((theme) => <small key={theme}>{theme}</small>)}</div>
              </article>
            ))}
            {selectedEvents.map((event) => (
              <article key={event.id} className="event-evidence">
                <span>{event.dateText || event.year} · EVENT</span>
                <strong>{event.title}</strong>
                <p>{event.summary}</p>
              </article>
            ))}
            {selectedDocuments.length === 0 && selectedEvents.length === 0 && (
              <p className="empty-evidence">No mock documents or events are linked to this place yet.</p>
            )}
          </div>
        </div>

        <div className="info-card">
          <div className="panel-header compact">
            <div>
              <p className="section-kicker">Filters</p>
              <h3>Statistic</h3>
            </div>
          </div>
          <div className="chip-group vertical">
            {allStats.map((mode) => (
              <button
                key={mode}
                type="button"
                className={mode === stat ? 'chip active' : 'chip'}
                aria-pressed={mode === stat}
                onClick={() => updateFilters({ statistic: mode })}
              >
                {mode}
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
