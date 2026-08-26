import React from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource/noto-sans-thai/400.css'
import '@fontsource/noto-sans-thai/500.css'
import '@fontsource/noto-sans-thai/600.css'
import '@fontsource/noto-sans-thai/700.css'
import './styles.css'
// Keep the public-menu stylesheet in the entry bundle. Some production hosts
// do not inject CSS emitted beside a conditionally imported page chunk.
import './pages/HongDuckMenu.css'

// QR menu pages are public and must not initialize Firebase Auth. Loading
// the authenticated app lazily also keeps its larger bundle out of the
// customer-facing menu.
const isHongDuckMenu = window.location.pathname === '/menu/hong-duck'
if (isHongDuckMenu) document.title = 'เมนู | ฮง เป็ดย่าง'
const appModule = isHongDuckMenu
  ? import('./pages/HongDuckMenu.jsx')
  : import('./App.jsx')

appModule.then(({ default: App }) => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
})
