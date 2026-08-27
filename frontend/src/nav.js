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
  { to: '/', icon: House, label: 'หน้าแรก', end: true },
  { to: '/materials', icon: Package, label: 'ของในครัว', short: 'ของในครัว',
    was: 'วัตถุดิบและสต๊อก' },
  { to: '/receiving', icon: ShoppingCart, label: 'ซื้อของเข้าร้าน', short: 'ซื้อของ',
    was: 'รับของเข้า' },
];

// Work that comes round every week or so, plus the records people look
// back at. One flat list rather than headed sections: with two or three
// entries each, the headings were taller than the things they organised
// and made a short menu look like a filing system.
export const REGULAR = [
  // "ของหายไปไหน" used to be its own page. It now lives inside นับของ,
  // because the result of a count belongs where the counting happens -
  // finishing a count and having to go find the answer somewhere else is
  // how people stopped looking at it.
  { to: '/stock-count', icon: ClipboardText, label: 'เช็กสต๊อกวัตถุดิบ' },
  // Used many times a day, but the phone's bottom bar only holds three
  // and the three it holds are used more. First in this list instead.
  { to: '/delivery-orders', icon: Moped, label: 'ออเดอร์นอกร้าน',
    needs: 'view_money' },
  { to: '/receipts', icon: Receipt, label: 'รายการขาย',
    was: 'รายการบิล', needs: 'view_money' },
  { to: '/income-expense', icon: Wallet, label: 'รายรับรายจ่าย',
    needs: 'view_money' },
  // Last in the list on purpose. It answers questions about the pages
  // above it, so it makes sense after them - and someone who has not
  // found the figures yet should find the figures first.
  { to: '/assistant', icon: ChatCircleText, label: 'ผู้ช่วยวิเคราะห์ร้าน',
    needs: 'view_money' },
];

// Things set up once and rarely touched again. Collapsed behind a single
// "ตั้งค่าระบบ" entry so four rarely-used links don't sit at the same
// weight as the pages someone opens every morning.
export const SETUP = {
  icon: GearSix,
  label: 'ตั้งค่าระบบ',
  items: [
    { to: '/recipes', icon: CookingPot, label: 'สูตรอาหาร' },
    { to: '/items', icon: Tag, label: 'เมนูในร้าน', was: 'รายการสินค้า' },
    { to: '/users', icon: Users, label: 'ผู้ใช้งาน', needs: 'manage_users' },
    { to: '/settings', icon: PlugsConnected, label: 'เชื่อมต่อ & สาขา', was: 'ตั้งค่า',
      needs: 'manage_settings' },
  ],
};

/** Everything below the daily three - used by the phone's "เพิ่มเติม" page. */
export const MORE_GROUPS = [
  { title: '', items: REGULAR },
  { title: SETUP.label, items: SETUP.items },
];

/** Phone bottom bar: the daily three, plus a way into everything else. */
export const TABS = [
  ...DAILY,
  { to: '/more', icon: List, label: 'เพิ่มเติม', short: 'เพิ่มเติม' },
];
import {
  House, Package, ShoppingCart, ClipboardText, Receipt, Wallet,
  GearSix, CookingPot, Tag, Users, PlugsConnected, List, Moped,
  ChatCircleText,
} from '@phosphor-icons/react';
