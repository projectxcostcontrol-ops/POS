export const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

/**
 * Every request carries the signed-in user's Firebase ID token, which
 * the backend verifies and maps to a role. Imported lazily to avoid a
 * circular import (firebase.js -> client.js -> firebase.js).
 */
async function authHeader() {
  try {
    const { auth } = await import('../firebase');
    const token = await auth.currentUser?.getIdToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

/**
 * A thrown API error keeps its status code, because 409 in particular is
 * not a failure - it's the backend saying "this person is signed in but
 * hasn't joined a business yet", which the app answers by showing the
 * signup screen rather than an error.
 */
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(await authHeader()) },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* plain text */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  getStores: () => request('/api/stores'),
  getItems: (storeId) => request(`/api/${storeId}/items`),
  getCategories: (storeId) => request(`/api/${storeId}/categories`),
  createCategory: (storeId, name) =>
    request(`/api/${storeId}/categories?name=${encodeURIComponent(name)}`, { method: 'POST' }),
  renameCategory: (storeId, categoryId, name) =>
    request(`/api/${storeId}/categories/${categoryId}?name=${encodeURIComponent(name)}`, { method: 'PUT' }),
  deleteCategory: (storeId, categoryId) =>
    request(`/api/${storeId}/categories/${categoryId}`, { method: 'DELETE' }),
  setItemCategory: (storeId, itemName, categoryId) =>
    request(`/api/${storeId}/items/${encodeURIComponent(itemName)}/category?category_id=${categoryId}`, { method: 'PUT' }),

  getMaterials: (storeId) => request(`/api/${storeId}/materials`),
  upsertMaterial: (storeId, materialId, data) =>
    request(`/api/${storeId}/materials/${materialId}`, { method: 'PUT', body: JSON.stringify(data) }),
  adjustStock: (storeId, materialId, newStock, reason = '') =>
    request(`/api/${storeId}/materials/${materialId}/adjust?new_stock=${newStock}&reason=${encodeURIComponent(reason)}`,
      { method: 'POST' }),

  getMovements: (storeId, materialId) =>
    request(`/api/${storeId}/materials/${materialId}/movements`),
  getCostHistory: (storeId, materialId) =>
    request(`/api/${storeId}/materials/${materialId}/cost-history`),
  recordWaste: (storeId, materialId, quantity, note = '') =>
    request(`/api/${storeId}/materials/${materialId}/waste?quantity=${quantity}&note=${encodeURIComponent(note)}`,
      { method: 'POST' }),
  migrateStock: (storeId) => request(`/api/${storeId}/migrate-stock`, { method: 'POST' }),

  /**
   * Loyverse accounts. A business can have several - shops that grew one
   * branch at a time often opened a separate Loyverse account for each,
   * so one token means one branch. The token goes in the body, not the
   * query string: query strings are written to access logs by every
   * proxy they pass through.
   */
  listConnections: () => request('/api/settings/connections'),
  addConnection: (token, label = '') =>
    request('/api/settings/connections', {
      method: 'POST', body: JSON.stringify({ token, label }),
    }),
  removeConnection: (connectionId) =>
    request(`/api/settings/connections/${connectionId}`, { method: 'DELETE' }),
  rebuildStockSnapshot: (storeId) =>
    request(`/api/${storeId}/rebuild-stock-snapshot`, { method: 'POST' }),

  getReceivings: (storeId) => request(`/api/${storeId}/receivings`),
  addReceiving: (storeId, data) =>
    request(`/api/${storeId}/receivings`, { method: 'POST', body: JSON.stringify(data) }),

  scanInvoice: async (storeId, file) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${BASE_URL}/api/${storeId}/receiving/scan`, {
      method: 'POST', body: form, headers: await authHeader(),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json();
  },

  /**
   * Orders the till never saw - Grab, the phone, the online menu. Saved
   * as ordinary sales so every report already counts them, and deducted
   * through the same recipes as a walk-in.
   *
   * The order id is generated here rather than by the server, which is
   * what makes a retry safe: a request that times out and is sent again
   * carries the same id and the second one is refused, instead of
   * deducting the same dish twice.
   */
  getDeliveryOrders: (storeId, from, to) =>
    request(`/api/${storeId}/delivery-orders?from_=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
  addDeliveryOrder: (storeId, order) =>
    request(`/api/${storeId}/delivery-orders`, {
      method: 'POST', body: JSON.stringify(order),
    }),
  deleteDeliveryOrder: (storeId, orderId) =>
    request(`/api/${storeId}/delivery-orders/${encodeURIComponent(orderId)}`,
      { method: 'DELETE' }),

  getMe: () => request('/api/me'),
  listUsers: () => request('/api/users'),
  /**
   * Invites go through the body, not the query string. An invite token is
   * a credential - whoever holds it can join the business with the role it
   * carries - and every proxy between here and the backend writes request
   * URLs to an access log, where it would outlive the invite itself. The
   * invited person's email is someone else's personal data and doesn't
   * belong in a log either.
   */
  inviteUser: (email, role, storeIds = []) =>
    request('/api/users/invites', {
      method: 'POST',
      body: JSON.stringify({ email, role, store_ids: storeIds }),
    }),
  cancelInvite: (token) =>
    request('/api/users/invites/cancel', {
      method: 'POST', body: JSON.stringify({ token }),
    }),
  updateUserRole: (uid, role, storeIds = []) =>
    request(`/api/users/${uid}?role=${role}&store_ids=${storeIds.join(',')}`, { method: 'PUT' }),
  removeUser: (uid) => request(`/api/users/${uid}`, { method: 'DELETE' }),
  listDrafts: (storeId) => request(`/api/${storeId}/receiving/drafts`),
  getDraft: (storeId, draftId) => request(`/api/${storeId}/receiving/drafts/${draftId}`),
  updateDraft: (storeId, draftId, data) =>
    request(`/api/${storeId}/receiving/drafts/${draftId}`, { method: 'PUT', body: JSON.stringify(data) }),
  discardDraft: (storeId, draftId) =>
    request(`/api/${storeId}/receiving/drafts/${draftId}`, { method: 'DELETE' }),
  confirmDraft: (storeId, draftId) =>
    request(`/api/${storeId}/receiving/drafts/${draftId}/confirm`, { method: 'POST' }),
  convertUnit: (storeId, item, materialId) =>
    request(`/api/${storeId}/receiving/convert-unit?material_id=${materialId}`,
      { method: 'POST', body: JSON.stringify(item) }),

  /**
   * The receipt photo can't be loaded by pointing an <img> at the endpoint:
   * a plain <img> request carries no headers, so it arrives at the backend
   * with no Authorization and gets rejected. Fetch it here instead, where
   * the token can be attached, and hand back a blob URL the <img> can use.
   * The caller must revokeObjectURL when it's done.
   */
  getDraftImageUrl: async (storeId, draftId) => {
    const res = await fetch(
      `${BASE_URL}/api/${storeId}/receiving/drafts/${draftId}/image`,
      { headers: await authHeader() },
    );
    if (!res.ok) {
      const text = await res.text();
      let detail = text;
      try { detail = JSON.parse(text).detail ?? text; } catch { /* plain text */ }
      throw new ApiError(res.status, detail);
    }
    return URL.createObjectURL(await res.blob());
  },

  // ---- AI recipe suggestions (3.3) ----
  suggestStatus: (storeId) => request(`/api/${storeId}/recipes/suggest/status`),
  suggestRecipe: (storeId, itemName) =>
    request(`/api/${storeId}/recipes/suggest?item_name=${encodeURIComponent(itemName)}`,
      { method: 'POST' }),
  listRecipeDrafts: (storeId) => request(`/api/${storeId}/recipes/drafts`),
  deleteRecipeDraft: (storeId, itemName) =>
    request(`/api/${storeId}/recipes/drafts/${encodeURIComponent(itemName)}`, { method: 'DELETE' }),
  listRecipeSkips: (storeId) => request(`/api/${storeId}/recipes/skips`),
  skipRecipe: (storeId, itemName) =>
    request(`/api/${storeId}/recipes/skips/${encodeURIComponent(itemName)}`, { method: 'POST' }),
  unskipRecipe: (storeId, itemName) =>
    request(`/api/${storeId}/recipes/skips/${encodeURIComponent(itemName)}`, { method: 'DELETE' }),

  // ---- sales reporting (reads our saved copy, not the POS) ----
  // One call for the whole sales screen. Splitting it meant the same
  // window of sales was fetched twice over.
  getSalesOverview: (storeId, from, to, granularity = 'day', top = 5) =>
    // getTimezoneOffset() counts the opposite way to what the API wants.
    request(`/api/${storeId}/sales/overview?from_=${encodeURIComponent(from)}` +
      `&to=${encodeURIComponent(to)}&granularity=${granularity}&top=${top}` +
      `&tz_offset=${-new Date().getTimezoneOffset()}`),
  getDailySales: (storeId, from, to) =>
    request(`/api/${storeId}/sales/daily?from_=${encodeURIComponent(from)}` +
      `&to=${encodeURIComponent(to)}`),
  getAlerts: (storeId) => request(`/api/${storeId}/alerts`),
  reconcileSales: (storeId, days = 1) =>
    request(`/api/${storeId}/sales/reconcile?days=${days}`),
  repairSales: (storeId) =>
    request(`/api/${storeId}/sales/repair`, { method: 'POST' }),

  // ---- stock counts & variance (3.4) ----
  listCounts: (storeId) => request(`/api/${storeId}/counts`),
  getOpenCount: (storeId) => request(`/api/${storeId}/counts/open`),
  startCount: (storeId) => request(`/api/${storeId}/counts`, { method: 'POST' }),
  setCountEntry: (storeId, sessionId, materialId, counted) =>
    request(`/api/${storeId}/counts/${sessionId}/entry?material_id=${materialId}&counted=${counted}`,
      { method: 'PUT' }),
  clearCountEntry: (storeId, sessionId, materialId) =>
    request(`/api/${storeId}/counts/${sessionId}/entry?material_id=${materialId}`,
      { method: 'DELETE' }),
  cancelCount: (storeId, sessionId) =>
    request(`/api/${storeId}/counts/${sessionId}`, { method: 'DELETE' }),
  closeCount: (storeId, sessionId) =>
    request(`/api/${storeId}/counts/${sessionId}/close`, { method: 'POST' }),
  getVariance: (storeId, sessionId) => request(`/api/${storeId}/variance/${sessionId}`),
  getVarianceSettings: (storeId) => request(`/api/${storeId}/variance-settings`),
  saveVarianceSettings: (storeId, pct, value) =>
    request(`/api/${storeId}/variance-settings?pct=${pct}&value=${value}`, { method: 'POST' }),

  getRecipe: (storeId, itemName) => request(`/api/${storeId}/recipes/${encodeURIComponent(itemName)}`),
  setRecipe: (storeId, itemName, ingredients) =>
    request(`/api/${storeId}/recipes/${encodeURIComponent(itemName)}`, {
      method: 'PUT', body: JSON.stringify(ingredients),
    }),

  getExpenses: (storeId, category) =>
    request(`/api/${storeId}/expenses${category ? `?category=${category}` : ''}`),
  updateExpense: (storeId, expenseId, { category, name, amount, date }) =>
    request(`/api/${storeId}/expenses/${expenseId}`, {
      method: 'PUT',
      body: JSON.stringify({ category, name, amount, date }),
    }),
  deleteExpense: (storeId, expenseId) =>
    request(`/api/${storeId}/expenses/${expenseId}`, { method: 'DELETE' }),
  addExpense: (storeId, { category, name, amount, date }) =>
    request(
      `/api/${storeId}/expenses?category=${category}&name=${encodeURIComponent(name)}&amount=${amount}&date=${date}`,
      { method: 'POST' }
    ),

  getReceipts: (storeId, from) =>
    request(`/api/${storeId}/receipts` +
      (from ? `?created_at_min=${encodeURIComponent(from)}` : '')),

  // ---- signup (before the user belongs to any business) ----
  signupBusiness: (businessName, displayName) =>
    request('/api/signup/business', {
      method: 'POST',
      body: JSON.stringify({ business_name: businessName, display_name: displayName }),
    }),
  peekInvite: (token) =>
    request('/api/invites/peek', { method: 'POST', body: JSON.stringify({ token }) }),
  signupStatus: () => request('/api/signup/status'),
  signupJoin: (token, displayName) =>
    request('/api/signup/join', {
      method: 'POST',
      body: JSON.stringify({ token, display_name: displayName }),
    }),

  // ---- our own back office ----
  adminWhoami: () => request('/api/admin/whoami'),
  adminOverview: () => request('/api/admin/overview'),

  getAppSettings: () => request('/api/settings'),
  saveBusinessName: (name) =>
    request(`/api/settings/business-name?name=${encodeURIComponent(name)}`, { method: 'POST' }),
  saveSyncInterval: (seconds) =>
    request(`/api/settings/sync-interval?seconds=${seconds}`, { method: 'POST' }),

  sync: (storeId) => request(`/api/${storeId}/sync`, { method: 'POST' }),
  resetSyncCursor: (storeId) =>
    request(`/api/${storeId}/sync/reset-cursor`, { method: 'POST' }),
  resetSyncCursor: (storeId) =>
    request(`/api/${storeId}/sync/reset-cursor`, { method: 'POST' }),
};
