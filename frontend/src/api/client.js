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

  getMe: () => request('/api/me'),
  listUsers: () => request('/api/users'),
  inviteUser: (email, role, storeIds = []) =>
    request(`/api/users/invite?email=${encodeURIComponent(email)}&role=${role}&store_ids=${storeIds.join(',')}`,
      { method: 'POST' }),
  cancelInvite: (token) =>
    request(`/api/users/invite?token=${encodeURIComponent(token)}`, { method: 'DELETE' }),
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
  suggestAllRecipes: (storeId, itemNames) =>
    request(`/api/${storeId}/recipes/suggest-all`,
      { method: 'POST', body: JSON.stringify({ item_names: itemNames }) }),
  listRecipeDrafts: (storeId) => request(`/api/${storeId}/recipes/drafts`),
  deleteRecipeDraft: (storeId, itemName) =>
    request(`/api/${storeId}/recipes/drafts/${encodeURIComponent(itemName)}`, { method: 'DELETE' }),
  listRecipeSkips: (storeId) => request(`/api/${storeId}/recipes/skips`),
  skipRecipe: (storeId, itemName) =>
    request(`/api/${storeId}/recipes/skips/${encodeURIComponent(itemName)}`, { method: 'POST' }),
  unskipRecipe: (storeId, itemName) =>
    request(`/api/${storeId}/recipes/skips/${encodeURIComponent(itemName)}`, { method: 'DELETE' }),

  getRecipe: (storeId, itemName) => request(`/api/${storeId}/recipes/${encodeURIComponent(itemName)}`),
  setRecipe: (storeId, itemName, ingredients) =>
    request(`/api/${storeId}/recipes/${encodeURIComponent(itemName)}`, {
      method: 'PUT', body: JSON.stringify(ingredients),
    }),

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
  closeCount: (storeId, sessionId) =>
    request(`/api/${storeId}/counts/${sessionId}/close`, { method: 'POST' }),
  getVariance: (storeId, sessionId) => request(`/api/${storeId}/variance/${sessionId}`),
  getVarianceSettings: (storeId) => request(`/api/${storeId}/variance-settings`),
  saveVarianceSettings: (storeId, pct, value) =>
    request(`/api/${storeId}/variance-settings?pct=${pct}&value=${value}`, { method: 'POST' }),

  getExpenses: (storeId, category) =>
    request(`/api/${storeId}/expenses${category ? `?category=${category}` : ''}`),
  addExpense: (storeId, { category, name, amount, date }) =>
    request(
      `/api/${storeId}/expenses?category=${category}&name=${encodeURIComponent(name)}&amount=${amount}&date=${date}`,
      { method: 'POST' }
    ),

  getReceipts: (storeId) => request(`/api/${storeId}/receipts`),

  // ---- signup (before the user belongs to any business) ----
  signupBusiness: (businessName, displayName) =>
    request('/api/signup/business', {
      method: 'POST',
      body: JSON.stringify({ business_name: businessName, display_name: displayName }),
    }),
  peekInvite: (token) => request(`/api/invites/${encodeURIComponent(token)}`),
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
  saveToken: (token) => request(`/api/settings/token?token=${encodeURIComponent(token)}`, { method: 'POST' }),
  disconnectToken: () => request('/api/settings/disconnect', { method: 'POST' }),
  saveSyncInterval: (seconds) =>
    request(`/api/settings/sync-interval?seconds=${seconds}`, { method: 'POST' }),

  sync: (storeId) => request(`/api/${storeId}/sync`, { method: 'POST' }),
};
