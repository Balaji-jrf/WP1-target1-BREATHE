import { useEffect, useMemo, useRef, useState } from 'react';
import Map, {
  FullscreenControl,
  GeolocateControl,
  Layer,
  NavigationControl,
  ScaleControl,
  Source,
} from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

const BASEMAPS = {
  light: {
    id: 'light',
    label: 'Light',
    tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
    attribution: '&copy; OpenStreetMap contributors',
    paint: { 'raster-saturation': -0.18, 'raster-brightness-max': 0.94 },
  },
  dark: {
    id: 'dark',
    label: 'Dark',
    tiles: ['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'],
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    paint: { 'raster-saturation': -0.1, 'raster-brightness-max': 0.9 },
  },
  satellite: {
    id: 'satellite',
    label: 'Satellite',
    tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution: 'Tiles &copy; Esri',
    paint: { 'raster-saturation': 0.1, 'raster-brightness-max': 0.92 },
  },
};

const STYLE = {
  version: 8,
  sources: {
    basemap: {
      type: 'raster',
      tiles: BASEMAPS.light.tiles,
      tileSize: 256,
      attribution: BASEMAPS.light.attribution,
    },
  },
  layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
};

const PALETTES = {
  lst: ['#3b0f70', '#8c2981', '#de4968', '#fe9f6d', '#fcfdbf'],
  ndvi: ['#f7f7d5', '#c7e9a4', '#73c476', '#238443', '#005a32'],
  ndbi: ['#f7f4f9', '#d4b9da', '#c994c7', '#df65b0', '#980043'],
  lulc: ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'],
  events: ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'],
};

function boundsPolygon([west, south, east, north]) {
  return [[
    [west, south],
    [east, south],
    [east, north],
    [west, north],
    [west, south],
  ]];
}

function makeGeoJson(places, getValue, selectedPlace) {
  const features = places.map((place) => ({
    type: 'Feature',
    id: place.id,
    properties: {
      placeId: place.id,
      name: place.name,
      value: getValue(place.id),
      selected: place.id === selectedPlace.id ? 1 : 0,
      contextOnly: false,
    },
    geometry: place.geometry || { type: 'Polygon', coordinates: boundsPolygon(place.bbox) },
  }));
  const activePlace = places.find((place) => place.id === selectedPlace.id);
  if (!activePlace) {
    // Keep the selected parent visible as an outline while displaying its children.
    features.push({
      type: 'Feature',
      id: selectedPlace.id,
      properties: {
        placeId: selectedPlace.id,
        name: selectedPlace.name,
        value: getValue(selectedPlace.id),
        selected: 1,
        contextOnly: true,
      },
      geometry: selectedPlace.geometry || { type: 'Polygon', coordinates: boundsPolygon(selectedPlace.bbox) },
    });
  }
  return {
    type: 'FeatureCollection',
    features,
  };
}

const eventGeoJson = (events) => ({
  type: 'FeatureCollection',
  features: events.filter((event) => event.location).map((event) => ({
    type: 'Feature',
    properties: { title: event.title, year: event.year, placeId: event.placeIds[0] },
    geometry: { type: 'Point', coordinates: event.location },
  })),
});

function colorExpression(layerId, domain) {
  const palette = PALETTES[layerId] || PALETTES.lst;
  const min = domain[0];
  const max = domain[1];
  const span = max - min || 1;
  return [
    'interpolate',
    ['linear'],
    ['coalesce', ['get', 'value'], min],
    min,
    palette[0],
    min + span * 0.25,
    palette[1],
    min + span * 0.5,
    palette[2],
    min + span * 0.75,
    palette[3],
    max,
    palette[4],
  ];
}

export default function AtlasMap({
  places,
  getValue,
  selectedPlace,
  activeLayer,
  activeMeta,
  events,
  basemap = 'light',
  onBasemapChange,
  onPlaceSelect,
  onMapError,
  hasData = false,
  compact = false,
}) {
  const mapRef = useRef(null);
  const hoveredId = useRef(null);
  const [hoveredPlace, setHoveredPlace] = useState(null);
  const [opacity, setOpacity] = useState(0.72);
  const mapData = useMemo(
    () => makeGeoJson(places, getValue, selectedPlace),
    [places, getValue, selectedPlace]
  );
  const eventData = useMemo(() => eventGeoJson(events), [events]);
  const basemapConfig = BASEMAPS[basemap] || BASEMAPS.light;

  useEffect(() => {
    if (!mapRef.current) return;
    mapRef.current.flyTo({
      center: selectedPlace.centroid,
      zoom: Math.max(6, selectedPlace.level === 'state' ? 6.2 : selectedPlace.level === 'district' ? 8.1 : 10),
      duration: 850,
    });
  }, [selectedPlace.id, selectedPlace.centroid, selectedPlace.level]);

  const onMouseMove = (event) => {
    const feature = event.features?.find((item) => item.layer.id === 'place-fills');
    if (hoveredId.current !== feature?.id) {
      if (hoveredId.current !== null) {
        mapRef.current?.setFeatureState({ source: 'places', id: hoveredId.current }, { hover: false });
      }
      hoveredId.current = feature?.id ?? null;
      if (hoveredId.current !== null) {
        mapRef.current?.setFeatureState({ source: 'places', id: hoveredId.current }, { hover: true });
      }
    }
    setHoveredPlace(feature?.properties || null);
    if (mapRef.current) {
      mapRef.current.getCanvas().style.cursor = feature ? 'pointer' : '';
    }
  };

  const onClick = (event) => {
    const feature = event.features?.find((item) => item.layer.id === 'place-fills');
    if (feature?.properties?.placeId) onPlaceSelect(feature.properties.placeId);
  };

  return (
    <div className={`atlas-map atlas-maplibre ${compact ? 'atlas-map-compact' : ''}`}>
      <Map
        ref={mapRef}
        initialViewState={{
          longitude: selectedPlace.centroid[0],
          latitude: selectedPlace.centroid[1],
          zoom: selectedPlace.level === 'state' ? 6.2 : selectedPlace.level === 'district' ? 8.1 : 10,
        }}
        aria-label={`${selectedPlace.name} ${activeMeta.label} map, ${activeMeta.unit}`}
        mapStyle={{
          ...STYLE,
          sources: {
            basemap: {
              ...STYLE.sources.basemap,
              tiles: basemapConfig.tiles,
              attribution: basemapConfig.attribution,
            },
          },
        }}
        workerUrl={`${process.env.PUBLIC_URL || ''}/maplibre-gl-worker.js`}
        interactiveLayerIds={['place-fills', ...(activeLayer === 'events' ? ['event-points'] : [])]}
        onMouseMove={onMouseMove}
        onMouseLeave={() => {
          if (hoveredId.current !== null) {
            mapRef.current?.setFeatureState({ source: 'places', id: hoveredId.current }, { hover: false });
            hoveredId.current = null;
          }
          setHoveredPlace(null);
        }}
        onClick={onClick}
        onError={(event) => onMapError?.(event.error)}
        onLoad={(event) => {
          event.target.setPaintProperty('basemap', 'raster-opacity', 1);
          Object.entries(basemapConfig.paint).forEach(([property, value]) => {
            event.target.setPaintProperty('basemap', property, value);
          });
        }}
        attributionControl
        reuseMaps
      >
        <Source id="places" type="geojson" data={mapData}>
          <Layer
            id="place-fills"
            type="fill"
            filter={['==', ['get', 'contextOnly'], false]}
            paint={{
              'fill-color': hasData ? colorExpression(activeLayer, activeMeta.domain) : '#86a997',
              'fill-color-transition': { duration: 350 },
              'fill-opacity': hasData
                ? ['case', ['boolean', ['feature-state', 'hover'], false], Math.min(opacity + 0.16, 1), opacity]
                : 0.38,
              'fill-outline-color': ['case', ['==', ['get', 'selected'], 1], '#fff8df', '#f8f4e8'],
            }}
          />
          <Layer
            id="place-borders"
            type="line"
            paint={{
              'line-color': ['case', ['==', ['get', 'selected'], 1], '#fff4d4', '#fff8df'],
              'line-width': ['case', ['==', ['get', 'selected'], 1], 3, 1.4],
              'line-opacity': 0.86,
            }}
          />
        </Source>

        {activeLayer === 'events' && (
          <Source id="events" type="geojson" data={eventData}>
            <Layer
              id="event-points"
              type="circle"
              paint={{
                'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 5, 11, 10],
                'circle-color': '#f07b53',
                'circle-stroke-color': '#fff7e8',
                'circle-stroke-width': 2,
              }}
            />
          </Source>
        )}

        <NavigationControl position="top-right" showCompass />
        {!compact && <GeolocateControl position="top-right" trackUserLocation />}
        {!compact && <FullscreenControl position="top-right" />}
        {!compact && <ScaleControl position="bottom-left" />}
      </Map>

      <div className="map-overlays">
        <label className="map-basemap-control">
          <span>Basemap</span>
          <select value={basemap} onChange={(event) => onBasemapChange(event.target.value)} aria-label="Basemap">
            {Object.values(BASEMAPS).map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
        {!compact && (
          <label className="map-opacity-control">
            <span>Layer opacity</span>
            <input type="range" min="0.25" max="1" step="0.05" value={opacity} onChange={(event) => setOpacity(Number(event.target.value))} />
          </label>
        )}
      </div>

      {hoveredPlace && (
        <div className="map-hover-card">
          <strong>{hoveredPlace.name}</strong>
          <span>{hasData ? `${Number(hoveredPlace.value).toFixed(activeLayer === 'ndvi' ? 3 : 1)} ${activeMeta.unit}` : 'No verified measurements loaded'}</span>
        </div>
      )}
    </div>
  );
}
