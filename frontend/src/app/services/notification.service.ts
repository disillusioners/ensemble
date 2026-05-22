import { Injectable, signal, inject, NgZone, OnDestroy } from '@angular/core';

// Notification interface matching backend SSE payload
export interface InstanceNotification {
  instance_id: string;
  agent_id: string;
  name: string;
  status: 'COMPLETED' | 'ERROR' | 'TERMINATED' | 'FAILED';
  timestamp: string;
}

// Internal notification for UI
export interface Notification extends InstanceNotification {
  id: string;  // Use instance_id as id
  read: boolean;
}

@Injectable({ providedIn: 'root' })
export class NotificationService implements OnDestroy {
  private readonly ngZone = inject(NgZone);
  private readonly API_BASE = '/api';
  
  // Signals for reactive state
  readonly notifications = signal<Notification[]>([]);
  readonly unreadCount = signal<number>(0);
  
  // SSE connection
  private eventSource: EventSource | null = null;
  private reconnectAttempts = 0;
  private readonly MAX_RECONNECT_DELAY = 30000;
  
  // Audio
  private audio: HTMLAudioElement | null = null;
  private audioUnlocked = false;
  private unlockHandler: (() => void) | null = null;

  constructor() {
    this.initAudio();
    this.setupAudioUnlock();
    this.connect();
  }
  
  ngOnDestroy(): void {
    this.disconnect();
  }
  
  private initAudio(): void {
    // Valid WAV chime: A5 (880Hz) sine wave with exponential decay, 120ms, 16kHz mono
    const chimeBase64 = 'data:audio/wav;base64,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';
    this.audio = new Audio(chimeBase64);
    this.audio.volume = 0.5;
  }

  private setupAudioUnlock(): void {
    const unlock = () => {
      if (this.audio && !this.audioUnlocked) {
        this.audio.play().then(() => {
          // Only remove listeners and cleanup on SUCCESS
          this.audioUnlocked = true;
          if (this.audio) {
            this.audio.pause();
            this.audio.currentTime = 0;
          }
          document.removeEventListener('click', unlock);
          document.removeEventListener('keydown', unlock);
          this.unlockHandler = null;
        }).catch(() => {
          // Keep listeners active for retry on next interaction
        });
      }
    };
    this.unlockHandler = unlock;
    document.addEventListener('click', unlock);
    document.addEventListener('keydown', unlock);
  }

  private playSound(): void {
    if (this.audio && this.audioUnlocked) {
      this.audio.currentTime = 0;
      this.audio.play().catch(() => {});
    }
  }
  
  connect(): void {
    if (this.eventSource) {
      return;
    }
    
    this.eventSource = new EventSource(`${this.API_BASE}/notifications/stream`);

    this.eventSource.onopen = () => {
      this.reconnectAttempts = 0;
    };
    
    this.eventSource.addEventListener('notification', (event: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data: InstanceNotification = JSON.parse(event.data);
          this.addNotification(data);
        } catch {
          // Ignore parse errors
        }
      });
    });

    this.eventSource.onerror = () => {
      this.ngZone.run(() => {
        this.disconnect();
        this.scheduleReconnect();
      });
    };
  }
  
  private scheduleReconnect(): void {
    const delay = Math.min(
      1000 * Math.pow(2, this.reconnectAttempts),
      this.MAX_RECONNECT_DELAY
    );
    this.reconnectAttempts++;
    
    setTimeout(() => {
      this.connect();
    }, delay);
  }
  
  disconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }
  
  private addNotification(notification: InstanceNotification): void {
    const newNotification: Notification = {
      ...notification,
      id: notification.instance_id,
      read: false,
    };

    this.notifications.update(list => {
      // Check if notification with same instance_id already exists
      const existingIndex = list.findIndex(n => n.instance_id === notification.instance_id);
      if (existingIndex !== -1) {
        // Replace existing notification instead of adding duplicate
        const updatedList = [...list];
        updatedList[existingIndex] = newNotification;
        return updatedList.slice(0, 50); // Keep last 50
      }
      // Add new notification
      return [newNotification, ...list].slice(0, 50); // Keep last 50
    });
    this.unreadCount.update(count => count + 1);
    this.playSound();
  }
  
  markAsRead(id: string): void {
    this.notifications.update(list =>
      list.map(n => (n.id === id ? { ...n, read: true } : n))
    );
    this.recalculateUnreadCount();
  }
  
  markAllAsRead(): void {
    this.notifications.update(list =>
      list.map(n => ({ ...n, read: true }))
    );
    this.unreadCount.set(0);
  }
  
  clearNotification(id: string): void {
    const wasUnread = this.notifications().find(n => n.id === id && !n.read);
    this.notifications.update(list => list.filter(n => n.id !== id));
    if (wasUnread) {
      this.recalculateUnreadCount();
    }
  }
  
  clearAll(): void {
    this.notifications.set([]);
    this.unreadCount.set(0);
  }
  
  private recalculateUnreadCount(): void {
    const count = this.notifications().filter(n => !n.read).length;
    this.unreadCount.set(count);
  }
}
