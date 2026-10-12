// Core data types for Karnataka Heat Atlas
// All components consume these shapes — swap mocks for real API later

/**
 * @typedef {'state'|'district'|'taluk'|'hobli'|'village'} AdminLevel
 * @typedef {'lst'|'ndvi'|'ndbi'|'lulc'|'events'} LayerId
 * @typedef {'mean'|'min'|'max'|'trend'} Statistic
 */

export const ADMIN_LEVELS = /** @type {const} */ (['state', 'district', 'taluk', 'hobli', 'village']);
export const LAYER_IDS = /** @type {const} */ (['lst', 'ndvi', 'ndbi', 'lulc', 'events']);
export const STATISTICS = /** @type {const} */ (['mean', 'min', 'max', 'trend']);
export const ENTITY_TYPES = /** @type {const} */ (['PLACE', 'PERSON', 'ORG', 'DATE']);

/**
 * @param {unknown} value
 * @returns {value is AdminLevel}
 */
export function isAdminLevel(value) {
  return typeof value === 'string' && ADMIN_LEVELS.includes(value);
}

/**
 * @param {unknown} value
 * @returns {value is LayerId}
 */
export function isLayerId(value) {
  return typeof value === 'string' && LAYER_IDS.includes(value);
}

/**
 * @param {unknown} value
 * @returns {value is Statistic}
 */
export function isStatistic(value) {
  return typeof value === 'string' && STATISTICS.includes(value);
}

/**
 * @param {unknown} value
 * @returns {value is 'PLACE'|'PERSON'|'ORG'|'DATE'}
 */
export function isEntityType(value) {
  return typeof value === 'string' && ENTITY_TYPES.includes(value);
}

/**
 * @typedef {Object} Place
 * @property {string} id
 * @property {string} name
 * @property {string} [nameKn]
 * @property {AdminLevel} level
 * @property {string|null} parentId
 * @property {[number,number,number,number]} bbox  [west,south,east,north]
 * @property {[number,number]} centroid  [lng,lat]
 */

/**
 * @typedef {Object} LayerMeta
 * @property {LayerId} id
 * @property {string} label
 * @property {string} unit
 * @property {number[]} years
 * @property {string} source
 * @property {string} sensor
 * @property {number} resolutionM
 * @property {string} [season]
 * @property {string} description
 * @property {string} citation
 * @property {string} colorRamp
 * @property {[number,number]} domain
 */

/**
 * @typedef {Object} PlaceStat
 * @property {string} placeId
 * @property {LayerId} layer
 * @property {number} year
 * @property {number} mean
 * @property {number} [min]
 * @property {number} [max]
 * @property {number} [trend]
 */

/**
 * @typedef {Object} DocumentRecord
 * @property {string} id
 * @property {string} title
 * @property {number|null} year
 * @property {string} docType
 * @property {string} source
 * @property {string} url
 * @property {string[]} placeIds
 * @property {string[]} themes
 * @property {{text:string,type:'PLACE'|'PERSON'|'ORG'|'DATE'}[]} entities
 * @property {string} snippet
 * @property {boolean} isMock
 */

/**
 * @typedef {Object} EventRecord
 * @property {string} id
 * @property {string} title
 * @property {number} year
 * @property {string} [dateText]
 * @property {string[]} placeIds
 * @property {string[]} themes
 * @property {string} summary
 * @property {string[]} documentIds
 * @property {[number,number]} [location]
 */
