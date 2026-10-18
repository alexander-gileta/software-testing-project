import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:3001').replace(/\/$/, '');
const MAX_VUS = Number(__ENV.MAX_VUS || 10);
const RESULT_FILE = __ENV.RESULT_FILE || 'results/single-summary.json';
const HEADERS = { Accept: 'application/json', 'Content-Type': 'application/json' };

if (!Number.isInteger(MAX_VUS) || MAX_VUS < 1 || MAX_VUS > 1000) {
  throw new Error('MAX_VUS must be an integer from 1 to 1000');
}

const listDuration = new Trend('list_duration', true);
const detailDuration = new Trend('detail_duration', true);
const createDuration = new Trend('create_duration', true);
const listErrors = new Rate('list_errors');
const detailErrors = new Rate('detail_errors');
const createErrors = new Rate('create_errors');
const scenarioErrors = new Rate('scenario_errors');
const listRequests = new Counter('list_requests');
const detailRequests = new Counter('detail_requests');
const createRequests = new Counter('create_requests');

export const options = {
  scenarios: {
    booking_usage: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: MAX_VUS },
        { duration: '90s', target: MAX_VUS },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '5s',
      gracefulStop: '5s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
    scenario_errors: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

function bookingPayload() {
  return {
    firstname: `Load${__VU}`,
    lastname: `Test${__ITER}`,
    totalprice: 100 + Math.floor(Math.random() * 900),
    depositpaid: Math.random() > 0.5,
    bookingdates: {
      checkin: '2026-11-01',
      checkout: '2026-11-05',
    },
    additionalneeds: 'Breakfast',
  };
}

function jsonOrNull(response) {
  try {
    return response.json();
  } catch (_) {
    return null;
  }
}

export function setup() {
  const ping = http.get(`${BASE_URL}/ping`, { tags: { name: 'SETUP /ping' } });
  if (ping.status !== 201) {
    throw new Error(`Health check failed: /ping returned ${ping.status}`);
  }
  const response = http.get(`${BASE_URL}/booking`, {
    headers: HEADERS,
    tags: { name: 'SETUP /booking' },
  });
  const bookings = jsonOrNull(response);
  if (response.status !== 200 || !Array.isArray(bookings)) {
    throw new Error('Setup failed: GET /booking did not return a JSON array');
  }
  if (bookings.length === 0) {
    throw new Error('No seeded bookings found. Start the API using npm start (SEED=true).');
  }
  const ids = bookings.map((item) => item.bookingid).filter((id) => Number.isInteger(id));
  if (ids.length === 0) {
    throw new Error('Setup failed: no valid booking IDs in GET /booking');
  }
  return { ids };
}

export default function (data) {
  const operation = Math.random();
  let response;
  let valid;

  if (operation < 0.5) {
    response = http.get(`${BASE_URL}/booking`, {
      headers: HEADERS,
      tags: { name: 'GET /booking', endpoint: 'list' },
    });
    const body = jsonOrNull(response);
    valid = response.status === 200 && Array.isArray(body);
    check(response, { 'GET /booking: 200 and array': () => valid });
    listRequests.add(1);
    listDuration.add(response.timings.duration);
    listErrors.add(!valid);
  } else if (operation < 0.8) {
    const id = data.ids[Math.floor(Math.random() * data.ids.length)];
    response = http.get(`${BASE_URL}/booking/${id}`, {
      headers: HEADERS,
      tags: { name: 'GET /booking/:id', endpoint: 'detail' },
    });
    const body = jsonOrNull(response);
    valid = response.status === 200 && body !== null && typeof body.firstname === 'string'
      && body.bookingdates !== null && typeof body.bookingdates === 'object';
    check(response, { 'GET /booking/:id: 200 and booking': () => valid });
    detailRequests.add(1);
    detailDuration.add(response.timings.duration);
    detailErrors.add(!valid);
  } else {
    const payload = bookingPayload();
    response = http.post(`${BASE_URL}/booking`, JSON.stringify(payload), {
      headers: HEADERS,
      tags: { name: 'POST /booking', endpoint: 'create' },
    });
    const body = jsonOrNull(response);
    valid = response.status === 200 && body !== null && Number.isInteger(body.bookingid)
      && body.booking !== null && typeof body.booking === 'object'
      && body.booking.firstname === payload.firstname;
    check(response, { 'POST /booking: 200 and created': () => valid });
    createRequests.add(1);
    createDuration.add(response.timings.duration);
    createErrors.add(!valid);
  }

  scenarioErrors.add(!valid);
  sleep(0.2 + Math.random() * 0.3);
}

export function handleSummary(data) {
  return {
    [RESULT_FILE]: JSON.stringify(data, null, 2),
    stdout: `Test finished. Total HTTP requests: ${data.metrics.http_reqs?.values?.count ?? 0}; ` +
      `RPS: ${data.metrics.http_reqs?.values?.rate?.toFixed(2) ?? 'n/a'}; ` +
      `p95: ${data.metrics.http_req_duration?.values?.['p(95)']?.toFixed(2) ?? 'n/a'} ms\n`,
  };
}
