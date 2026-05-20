import { Component, inject, computed, signal, HostListener, ElementRef, OnDestroy, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { MatBadgeModule } from '@angular/material/badge';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NotificationService, Notification } from '../../services/notification.service';

@Component({
  selector: 'app-notification-bell',
  standalone: true,
  imports: [CommonModule, MatIconModule, MatButtonModule, MatMenuModule, MatBadgeModule, MatTooltipModule],
  templateUrl: './notification-bell.component.html',
  styleUrl: './notification-bell.component.scss'
})
export class NotificationBellComponent implements OnDestroy {
  private readonly notificationService = inject(NotificationService);
  private readonly elementRef = inject(ElementRef);

  // State
  isOpen = signal(false);
  isAnimating = signal(false);

  // Derived state
  notifications = this.notificationService.notifications;
  unreadCount = this.notificationService.unreadCount;
  hasUnread = computed(() => this.unreadCount() > 0);

  // Track previous count to trigger animation on new notification
  private previousUnreadCount = 0;

  // Animation timeout for cleanup
  private animationTimeout: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    // Watch for new notifications to trigger animation using Angular effect
    effect(() => {
      const count = this.unreadCount();
      if (count > this.previousUnreadCount) {
        this.triggerAnimation();
      }
      this.previousUnreadCount = count;
    });
  }

  ngOnDestroy(): void {
    if (this.animationTimeout !== null) {
      clearTimeout(this.animationTimeout);
      this.animationTimeout = null;
    }
  }
  
  private triggerAnimation(): void {
    this.isAnimating.set(true);
    this.animationTimeout = setTimeout(() => {
      this.isAnimating.set(false);
    }, 1000);
  }
  
  getStatusIcon(status: string): string {
    switch (status) {
      case 'COMPLETED': return 'check_circle';
      case 'ERROR': return 'error';
      case 'TERMINATED': return 'stop_circle';
      case 'FAILED': return 'cancel';
      default: return 'info';
    }
  }
  
  getStatusColor(status: string): string {
    switch (status) {
      case 'COMPLETED': return '#10b981';
      case 'ERROR': return '#f43f5e';
      case 'TERMINATED': return '#f59e0b';
      case 'FAILED': return '#f43f5e';
      default: return '#94a3b8';
    }
  }
  
  getRelativeTime(timestamp: string): string {
    const now = new Date();
    const date = new Date(timestamp);
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);
    
    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return date.toLocaleDateString();
  }
  
  truncateId(id: string): string {
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }
  
  onBellClick(): void {
    this.isOpen.set(!this.isOpen());
  }
  
  onNotificationClick(notification: Notification): void {
    this.notificationService.markAsRead(notification.id);
  }
  
  onClearNotification(event: Event, id: string): void {
    event.stopPropagation();
    this.notificationService.clearNotification(id);
  }
  
  onClearAll(): void {
    this.notificationService.clearAll();
  }
  
  onMarkAllRead(): void {
    this.notificationService.markAllAsRead();
  }
  
  // Click outside detection
  @HostListener('document:click', ['$event'])
  onDocumentClick(event: Event): void {
    if (!this.elementRef.nativeElement.contains(event.target)) {
      this.isOpen.set(false);
    }
  }
}
