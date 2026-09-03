/**
 * “当前宠物”的全局状态。
 * 首页展示当前宠物的健康概览，宠物页点击卡片即可切换。
 */

import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';

import { pets, type Pet } from '@/data/pets';

type CurrentPetContextValue = {
  currentPet: Pet;
  setCurrentPet: (pet: Pet) => void;
};

const CurrentPetContext = createContext<CurrentPetContextValue | undefined>(undefined);

export function CurrentPetProvider({ children }: { children: ReactNode }) {
  const [currentPet, setCurrentPet] = useState<Pet>(pets[0]);

  const value = useMemo(() => ({ currentPet, setCurrentPet }), [currentPet]);

  return <CurrentPetContext.Provider value={value}>{children}</CurrentPetContext.Provider>;
}

export function useCurrentPet() {
  const context = useContext(CurrentPetContext);
  if (!context) {
    throw new Error('useCurrentPet 必须在 <CurrentPetProvider> 内部使用');
  }
  return context;
}
