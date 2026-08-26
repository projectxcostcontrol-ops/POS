import { useEffect, useMemo, useState } from 'react';
import { BowlFood, ChatCircleDots, ForkKnife, MapPin, Minus, Phone, Plus, Receipt, Trash, X } from '@phosphor-icons/react';

const PHONE_DISPLAY = '082-6516461';
const PHONE_LINK = '0826516461';
const LINE_OA_ID = '@862uzpje';
const STORE_TIME_ZONE = 'Asia/Bangkok';
const STORE_OPEN_MINUTES = 8 * 60;
const STORE_CLOSE_MINUTES = 18 * 60;

function isStoreOpenNow() {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: STORE_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date());
  const hour = Number(parts.find((part) => part.type === 'hour')?.value || 0);
  const minute = Number(parts.find((part) => part.type === 'minute')?.value || 0);
  const currentMinutes = (hour * 60) + minute;
  return currentMinutes >= STORE_OPEN_MINUTES && currentMinutes < STORE_CLOSE_MINUTES;
}

const riceItems = [
  { name: 'ข้าวหน้าเป็ดย่าง', normal: 60, special: 70 },
  { name: 'ข้าวหมูแดง', normal: 60, special: 70 },
  { name: 'ข้าวหมูกรอบ', normal: 60, special: 70 },
  { name: 'ข้าวขาหมู', normal: 60, special: 70 },
  { name: 'ข้าวรวมสองอย่าง', normal: 60, special: 70 },
  { name: 'ข้าวรวมสามอย่าง', normal: 70, special: 80, note: 'เพิ่ม 10 บาท' },
];

const noodleItems = [
  { name: 'บะหมี่เป็ดย่าง', normal: 60, special: 70 },
  { name: 'บะหมี่หมูแดง', normal: 60, special: 70 },
  { name: 'บะหมี่หมูกรอบ', normal: 60, special: 70 },
  { name: 'บะหมี่ขาหมู', normal: 60, special: 70 },
  { name: 'บะหมี่รวมสองอย่าง', normal: 60, special: 70 },
  { name: 'บะหมี่รวมสามอย่าง', normal: 70, special: 80, note: 'เพิ่ม 10 บาท' },
];

const sideItems = [
  { name: 'เป็ดย่าง', normal: 150, special: 250 },
  { name: 'หมูแดง', normal: 80, special: 150 },
  { name: 'หมูกรอบ', normal: 80, special: 150 },
  { name: 'ขาหมู', normal: 80, special: 120 },
];

export default function HongDuckMenu() {
  const [activeSection, setActiveSection] = useState('rice');
  const [orderItems, setOrderItems] = useState({});
  const [orderOpen, setOrderOpen] = useState(false);
  const [storeOpen, setStoreOpen] = useState(isStoreOpenNow);

  useEffect(() => {
    const updateStoreStatus = () => setStoreOpen(isStoreOpenNow());
    const timer = window.setInterval(updateStoreStatus, 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const orderList = useMemo(() => Object.values(orderItems), [orderItems]);
  const orderCount = orderList.reduce((sum, item) => sum + item.quantity, 0);
  const orderTotal = orderList.reduce((sum, item) => sum + (item.price * item.quantity), 0);
  const lineOrderLink = useMemo(() => {
    const itemLines = orderList.map((item, index) =>
      `${index + 1}. ${item.name} (${item.variant}) x${item.quantity} — ${item.price * item.quantity} บาท`
    );
    const message = [
      'สวัสดีครับ/ค่ะ ต้องการสั่งอาหารจากร้านฮง เป็ดย่าง',
      '',
      ...itemLines,
      '',
      `รวม ${orderCount} รายการ`,
      `ยอดรวม ${orderTotal} บาท`,
    ].join('\n');

    return `https://line.me/R/oaMessage/${encodeURIComponent(LINE_OA_ID)}/?${encodeURIComponent(message)}`;
  }, [orderCount, orderList, orderTotal]);

  const addItem = (item, size) => {
    const key = `${item.name}-${size}`;
    const variant = size === 'normal' ? 'ธรรมดา' : 'พิเศษ';
    setOrderItems((current) => ({
      ...current,
      [key]: {
        key,
        name: item.name,
        variant,
        price: item[size],
        quantity: (current[key]?.quantity || 0) + 1,
      },
    }));
  };

  const changeQuantity = (key, change) => {
    setOrderItems((current) => {
      const nextQuantity = (current[key]?.quantity || 0) + change;
      if (nextQuantity <= 0) {
        const next = { ...current };
        delete next[key];
        return next;
      }
      return { ...current, [key]: { ...current[key], quantity: nextQuantity } };
    });
  };

  const removeItem = (key) => {
    setOrderItems((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  };

  return (
    <div className="hong-menu-page">
      <header className="hong-hero">
        <div className="hong-brand-row">
          <img className="hong-brand-logo" src="/menu/hong-duck/logo-transparent.png" width="300" height="270" alt="ฮง เป็ดย่าง SINCE 2022" />
          <div className="hong-status-block">
            <span className={`hong-open${storeOpen ? '' : ' closed'}`} title="เวลาเปิดร้าน 08:00–18:00 น."><i />{storeOpen ? 'เปิดอยู่' : 'ปิดอยู่'}</span>
            <span className="hong-branch"><MapPin size={14} />สาขาสี่แยกวิทยาลัยพยาบาล</span>
          </div>
        </div>
        <img src="/menu/hong-duck/hero-food.webp" width="1200" height="800" decoding="async" fetchpriority="high" alt="เป็ดย่าง บะหมี่ และหมูกรอบของร้านฮง เป็ดย่าง" />
      </header>

      <nav className="hong-categories" aria-label="หมวดเมนู">
        <div className="hong-categories-inner">
          <button className={activeSection === 'rice' ? 'active' : ''} onClick={() => setActiveSection('rice')}><BowlFood size={21} />ข้าว</button>
          <button className={activeSection === 'noodles' ? 'active' : ''} onClick={() => setActiveSection('noodles')}><ForkKnife size={21} />บะหมี่</button>
          <button className={activeSection === 'sides' ? 'active' : ''} onClick={() => setActiveSection('sides')}><CookingMark />กับข้าว</button>
        </div>
      </nav>

      <main className="hong-menu-content">
        {activeSection === 'rice' && (
          <section className="hong-menu-section" id="rice">
            <SectionHeading icon={<BowlFood size={25} />} title="เมนูข้าว" price="ธรรมดา 60 · พิเศษ 70 บาท" />
            <MenuRows items={riceItems} orderItems={orderItems} onAdd={addItem} />
          </section>
        )}

        {activeSection === 'noodles' && (
          <section className="hong-menu-section" id="noodles">
            <SectionHeading icon={<ForkKnife size={25} />} title="เมนูบะหมี่" price="ธรรมดา 60 · พิเศษ 70 บาท" />
            <MenuRows items={noodleItems} orderItems={orderItems} onAdd={addItem} />
          </section>
        )}

        {activeSection === 'sides' && (
          <section className="hong-menu-section" id="sides">
            <SectionHeading icon={<CookingMark />} title="เมนูกับข้าว" price="ธรรมดา · พิเศษ" />
            <MenuRows items={sideItems} orderItems={orderItems} onAdd={addItem} />
          </section>
        )}

        <footer className="hong-footer">
          <strong>ฮง เป็ดย่าง</strong>
          <span><MapPin size={15} />สาขาสี่แยกวิทยาลัยพยาบาล</span>
        </footer>
      </main>

      <div className="hong-call-dock">
        <button type="button" onClick={() => setOrderOpen(true)} aria-label={`เปิดรายการสั่งอาหาร ${orderCount} รายการ`}>
          <span className="hong-order-icon"><Receipt size={25} weight="fill" />{orderCount > 0 && <b>{orderCount}</b>}</span>
          <span><small>รายการสั่งอาหาร</small><strong>{orderCount > 0 ? `${orderCount} รายการ · ฿${orderTotal}` : 'เลือกราคาเพื่อเพิ่มรายการ'}</strong></span>
        </button>
      </div>

      {orderOpen && (
        <div className="hong-order-overlay" role="presentation" onClick={() => setOrderOpen(false)}>
          <section className="hong-order-sheet" role="dialog" aria-modal="true" aria-labelledby="hong-order-title" onClick={(event) => event.stopPropagation()}>
            <header>
              <div><p>สรุปรายการ</p><h2 id="hong-order-title">รายการสั่งอาหาร</h2></div>
              <button type="button" className="hong-close-order" onClick={() => setOrderOpen(false)} aria-label="ปิดรายการสั่งอาหาร"><X size={22} /></button>
            </header>

            <div className="hong-order-body">
              {orderList.length === 0 ? (
                <div className="hong-empty-order"><Receipt size={40} /><strong>ยังไม่มีรายการอาหาร</strong><span>แตะราคาของเมนูที่ต้องการเพื่อเพิ่มรายการ</span></div>
              ) : orderList.map((item) => (
                <div className="hong-order-row" key={item.key}>
                  <div className="hong-order-name"><strong>{item.name}</strong><span>{item.variant} · ฿{item.price}</span></div>
                  <div className="hong-quantity" aria-label={`จำนวน ${item.name}`}>
                    <button type="button" onClick={() => changeQuantity(item.key, -1)} aria-label={`ลดจำนวน ${item.name}`}><Minus size={17} /></button>
                    <strong>{item.quantity}</strong>
                    <button type="button" onClick={() => changeQuantity(item.key, 1)} aria-label={`เพิ่มจำนวน ${item.name}`}><Plus size={17} /></button>
                  </div>
                  <strong className="hong-line-total">฿{item.price * item.quantity}</strong>
                  <button type="button" className="hong-remove-item" onClick={() => removeItem(item.key)} aria-label={`ลบ ${item.name}`}><Trash size={18} /></button>
                </div>
              ))}
            </div>

            <footer className="hong-order-footer">
              <div className="hong-order-total"><span>รวม {orderCount} รายการ</span><strong>฿{orderTotal}</strong></div>
              {orderCount > 0 ? (
                <div className="hong-order-actions">
                  <a className="hong-line-order" href={lineOrderLink}>
                    <ChatCircleDots size={24} weight="fill" />
                    <span><small>ส่งรายการให้ทางร้าน</small><strong>สั่งผ่าน LINE</strong></span>
                  </a>
                  <a className="hong-phone-order" href={`tel:${PHONE_LINK}`}>
                    <Phone size={22} weight="fill" />
                    <span><small>หรือโทรสั่งรายการนี้</small><strong>{PHONE_DISPLAY}</strong></span>
                  </a>
                </div>
              ) : (
                <button type="button" onClick={() => setOrderOpen(false)}>กลับไปเลือกเมนู</button>
              )}
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}

function SectionHeading({ icon, title, price }) {
  return <div className="hong-section-heading"><span className="hong-heading-icon">{icon}</span><div><h2>{title}</h2><p>{price}</p></div></div>;
}

function MenuRows({ items, orderItems, onAdd }) {
  return (
    <div className="hong-menu-list">
      <div className="hong-price-head"><span>รายการ</span><span>ธรรมดา</span><span>พิเศษ</span></div>
      {items.map((item) => (
        <div className="hong-menu-row" key={item.name}>
          <div><strong>{item.name}</strong>{item.note && <small>{item.note}</small>}</div>
          <PriceButton item={item} size="normal" orderItems={orderItems} onAdd={onAdd} />
          <PriceButton item={item} size="special" orderItems={orderItems} onAdd={onAdd} special />
        </div>
      ))}
    </div>
  );
}

function PriceButton({ item, size, orderItems, onAdd, special = false }) {
  const quantity = orderItems[`${item.name}-${size}`]?.quantity || 0;
  const label = size === 'normal' ? 'ธรรมดา' : 'พิเศษ';
  return (
    <button type="button" className={`hong-price-button${special ? ' special' : ''}${quantity ? ' selected' : ''}`}
      onClick={() => onAdd(item, size)} aria-label={`เพิ่ม ${item.name} ${label} ราคา ${item[size]} บาท จำนวนปัจจุบัน ${quantity}`}>
      <span>{item[size]}<small>บาท</small></span>
      {quantity > 0 && <b>{quantity}</b>}
    </button>
  );
}

function CookingMark() {
  return <BowlFood size={22} aria-hidden="true" />;
}
