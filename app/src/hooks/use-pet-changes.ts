import { useEffect } from 'react';

import { useAuth } from '@/hooks/use-auth';
import { subscribePetChanges } from '@/services/pet-profile-events';

export function usePetChanges(onChange: () => void): void {
  const { user } = useAuth();
  useEffect(() => {
    if (user) return subscribePetChanges(user.id, onChange);
  }, [user, onChange]);
}
