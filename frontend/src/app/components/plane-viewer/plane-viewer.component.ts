import { Component, Input, OnInit } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

@Component({
  selector: 'app-plane-viewer',
  standalone: true,
  template: `
    @if (safeUrl) {
      <iframe [src]="safeUrl" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" style="width:100%; height:100%; border:none; display:block;"></iframe>
    }
  `,
})
export class PlaneViewerComponent implements OnInit {
  @Input() url: string = '';

  safeUrl: SafeResourceUrl | null = null;

  constructor(private sanitizer: DomSanitizer) {}

  ngOnInit(): void {
    if (this.url && /^https?:\/\//i.test(this.url)) {
      this.safeUrl = this.sanitizer.bypassSecurityTrustResourceUrl(this.url);
    }
  }
}
