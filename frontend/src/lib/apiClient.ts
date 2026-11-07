// API client: the single place the frontend talks to the backend. TODO: auth, timeouts, retries.
import { env } from './env';

export const apiBaseUrl = env.apiUrl;
