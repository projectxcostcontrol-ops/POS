import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { api } from '../api/client';

const StoreContext = createContext(null);

export function StoreProvider({ children }) {
  const [stores, setStores] = useState([]);
  const [storeId, setStoreId] = useState(() => localStorage.getItem('activeStoreId') || '');
  const [loading, setLoading] = useState(true);

  const refreshStores = useCallback(() => {
    setLoading(true);
    return api.getStores().then((list) => {
      setStores(list);
      setStoreId((current) => current || (list.length > 0 ? list[0].id : ''));
      setLoading(false);
      return list;
    }).catch(() => {
      setStores([]);
      setLoading(false);
      return [];
    });
  }, []);

  useEffect(() => {
    refreshStores();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function selectStore(id) {
    setStoreId(id);
    localStorage.setItem('activeStoreId', id);
  }

  function clearStores() {
    setStores([]);
    setStoreId('');
    localStorage.removeItem('activeStoreId');
  }

  return (
    <StoreContext.Provider value={{ stores, storeId, selectStore, loading, refreshStores, clearStores }}>
      {children}
    </StoreContext.Provider>
  );
}

export function useStore() {
  return useContext(StoreContext);
}
