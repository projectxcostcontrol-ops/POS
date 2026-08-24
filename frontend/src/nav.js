/**
 * Navigation, in one place.
 *
 * The sidebar, the phone tab bar, and the "เพิ่มเติม" page all read from
 * here, so a page renamed once is renamed everywhere. Splitting this
 * across three components is how a menu ends up saying "นับสต๊อก" in one
 * place and "นับของ" in another.
 *
 * `needs` is the capability required to see an entry. It drives the menu
 * only - the backend enforces the same rules independently, so hiding a
 * link is convenience, not security.
 *
 * `was` is the old name, shown in small text during the rename so anyone
 * who learned the old label can still find the page. Delete these fields
 * once the new names have stuck.
 */

// The three things a restaurant does every day. These get the phone's
// bottom bar, where they're reachable with one thumb.
export const DAILY = [
  { to: '/', emoji: '🏠', label: 'หน้าแรก', end: true },
  { to: '/materials', emoji: '📦', label: 'ของในครัว', short: 'ของในครัว',
    was: 'วัตถุดิบและสต๊อก' },
  { to: '/receiving', emoji: '🛒', label: 'ซื้อของเข้าร้าน', short: 'ซื้อของ',
    was: 'รับของเข้า' },
];

export const MORE_GROUPS = [
  {
    title: 'ทุกสัปดาห์',
    items: [
      { to: '/stock-count', emoji: '📋', label: 'นับของ', was: 'นับสต๊อก' },
      { to: '/variance', emoji: '🔍', label: 'ของหายไปไหน',
        was: 'วิเคราะห์ส่วนต่าง', needs: 'view_money' },
    ],
  },
  {
    title: 'ดูย้อนหลัง',
    items: [
      { to: '/receipts', emoji: '🧾', label: 'ยอดขาย',
        was: 'รายการบิล', needs: 'view_money' },
      { to: '/income-expense', emoji: '💰', label: 'รายรับรายจ่าย',
        needs: 'view_money' },
    ],
  },
  {
    title: 'ตั้งค่าครั้งเดียว',
    items: [
      { to: '/recipes', emoji: '🍳', label: 'สูตรอาหาร' },
      { to: '/items', emoji: '🏷️', label: 'เมนูในร้าน', was: 'รายการสินค้า' },
      { to: '/users', emoji: '👥', label: 'ผู้ใช้งาน', needs: 'manage_users' },
      { to: '/settings', emoji: '⚙️', label: 'ตั้งค่า', needs: 'manage_settings' },
    ],
  },
];

/** Sidebar layout: daily work first, then the same groups as the More page. */
export const SIDEBAR_GROUPS = [
  { title: 'ทุกวัน', items: DAILY },
  ...MORE_GROUPS,
];

/** Phone bottom bar: the daily three, plus a way into everything else. */
export const TABS = [
  ...DAILY,
  { to: '/more', emoji: '☰', label: 'เพิ่มเติม', short: 'เพิ่มเติม' },
];
