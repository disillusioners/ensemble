import { Component, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { Inject } from '@angular/core';
import type { Source, SourceType, SourceUpdate, Agent } from '../../models';
import { ApiService } from '../../services/api.service';

type ConfigTab = 'simple' | 'json';

interface SourceTypeOption {
  value: SourceType;
  label: string;
  icon: string;
  description: string;
}

// Option for select fields
interface SelectOption {
  value: string;
  label: string;
}

// Simple form field definitions per source type
interface SimpleField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'number' | 'checkbox' | 'select';
  placeholder: string;
  hint?: string;
  required?: boolean;
  section: 'credentials' | 'config';
  defaultValue?: string | number | boolean;
  options?: SelectOption[]; // For select type fields
}

interface SourceTypeConfig {
  fields: SimpleField[];
}

@Component({
  selector: 'app-edit-source-modal',
  standalone: true,
  imports: [CommonModule, FormsModule, MatDialogModule],
  templateUrl: './edit-source-modal.html',
  styleUrl: './edit-source-modal.scss'
})
export class EditSourceModalComponent implements OnInit {
  protected readonly sourceId = signal('');
  protected readonly sourceType = signal<SourceType>('telegram');
  protected readonly name = signal('');
  protected readonly configJson = signal('');
  protected readonly credentialsJson = signal('');
  protected readonly enabled = signal(true);
  protected readonly isLoading = signal(false);
  protected readonly error = signal<string | null>(null);
  
  // Test connection state
  protected readonly isTesting = signal(false);
  protected readonly testResult = signal<{ success: boolean; message: string } | null>(null);
  
  // Tab state for config section
  protected readonly configTab = signal<ConfigTab>('simple');
  
  // Simple form field values (key -> value)
  protected readonly simpleFieldValues = signal<Record<string, string | number | boolean>>({});
  
  // Agent list for select dropdown
  protected readonly agents = signal<Agent[]>([]);

  // Base source type configurations (without dynamic options)
  private readonly baseSourceTypeConfigs: Record<SourceType, Omit<SourceTypeConfig, 'fields'> & { fields: SimpleField[] }> = {
    telegram: {
      fields: [
        { key: 'bot_token', label: 'Bot Token', type: 'password', placeholder: 'Enter bot token (leave empty to keep current)', hint: 'Get from @BotFather on Telegram', required: false, section: 'credentials' },
        { key: 'secret_token', label: 'Secret Token', type: 'text', placeholder: 'my-secret-token', hint: 'Optional: For webhook verification', section: 'config' },
        { key: 'default_agent', label: 'Default Agent', type: 'select', placeholder: 'Select an agent...', hint: 'Agent to handle incoming messages', section: 'config', options: [] },
        { key: 'polling_enabled', label: 'Enable Polling', type: 'checkbox', placeholder: '', hint: 'Use polling instead of webhooks', section: 'config', defaultValue: true },
        { key: 'polling_timeout', label: 'Polling Timeout (sec)', type: 'number', placeholder: '30', hint: 'Long polling timeout', section: 'config', defaultValue: 30 },
      ]
    },
    webhook: {
      fields: [
        { key: 'webhook_url', label: 'Webhook URL Path', type: 'text', placeholder: '/webhook/my-endpoint', hint: 'URL path for receiving requests', required: true, section: 'config' },
        { key: 'secret_key', label: 'Secret Key', type: 'password', placeholder: 'Enter secret key (leave empty to keep current)', hint: 'Optional: For request verification', section: 'credentials' },
      ]
    },
    whatsapp: {
      fields: [
        { key: 'phone_number_id', label: 'Phone Number ID', type: 'text', placeholder: '123456789', hint: 'From WhatsApp Business API', required: true, section: 'credentials' },
        { key: 'access_token', label: 'Access Token', type: 'password', placeholder: 'Enter access token (leave empty to keep current)', hint: 'Permanent access token', required: false, section: 'credentials' },
        { key: 'verify_token', label: 'Verify Token', type: 'text', placeholder: 'my-verify-token', hint: 'For webhook verification', section: 'config' },
      ]
    },
    discord: {
      fields: [
        { key: 'bot_token', label: 'Bot Token', type: 'password', placeholder: 'Enter bot token (leave empty to keep current)', hint: 'From Discord Developer Portal', required: false, section: 'credentials' },
        { key: 'application_id', label: 'Application ID', type: 'text', placeholder: '123456789', hint: 'From Discord Developer Portal', required: true, section: 'credentials' },
        { key: 'guild_id', label: 'Server ID (Guild)', type: 'text', placeholder: '987654321', hint: 'Optional: Restrict to specific server', section: 'config' },
      ]
    },
    slack: {
      fields: [
        { key: 'bot_token', label: 'Bot Token', type: 'password', placeholder: 'Enter bot token (leave empty to keep current)', hint: 'Slack Bot User OAuth Token', required: false, section: 'credentials' },
        { key: 'app_token', label: 'App Token', type: 'password', placeholder: 'Enter app token (leave empty to keep current)', hint: 'Slack App-Level Token (for Socket Mode)', required: false, section: 'credentials' },
        { key: 'default_agent', label: 'Default Agent', type: 'select', placeholder: 'Select an agent...', hint: 'Agent to handle incoming messages', section: 'config', options: [] },
      ]
    },
    scheduler: {
      fields: [
        { key: 'schedule_type', label: 'Schedule Type', type: 'select', placeholder: 'Select type...', hint: 'How to schedule the job', section: 'config', options: [
          { value: 'cron', label: 'Cron Expression' },
          { value: 'interval', label: 'Interval (seconds)' },
          { value: 'one-time', label: 'One-time' }
        ]},
        { key: 'cron_expression', label: 'Cron Expression', type: 'text', placeholder: '0 9 * * 1-5', hint: 'e.g., 0 9 * * 1-5 for weekdays at 9 AM', section: 'config' },
        { key: 'interval_seconds', label: 'Interval (seconds)', type: 'number', placeholder: '3600', hint: 'e.g., 3600 for hourly', section: 'config' },
        { key: 'run_at', label: 'Run At', type: 'text', placeholder: '2025-12-25T10:00:00Z', hint: 'ISO datetime for one-time execution', section: 'config' },
        { key: 'agent', label: 'Agent', type: 'select', placeholder: 'Select an agent...', hint: 'Agent to handle the scheduled task', section: 'config' },
        { key: 'message', label: 'Message', type: 'text', placeholder: 'Task description or command', hint: 'The message to send to the agent', section: 'config' },
        { key: 'timezone', label: 'Timezone', type: 'text', placeholder: 'UTC', hint: 'Timezone for schedule (default: UTC)', section: 'config' },
      ]
    }
  };
  
  // Get source type configs with dynamic agent options populated
  private get sourceTypeConfigs(): Record<SourceType, SourceTypeConfig> {
    const agentOptions: SelectOption[] = this.agents().map(agent => ({
      value: agent.id,
      label: agent.name
    }));
    
    const configs: Record<SourceType, SourceTypeConfig> = {} as Record<SourceType, SourceTypeConfig>;
    for (const [type, config] of Object.entries(this.baseSourceTypeConfigs)) {
      configs[type as SourceType] = {
        fields: config.fields.map(field => {
          if (field.key === 'default_agent') {
            return { ...field, options: agentOptions };
          }
          return field;
        })
      };
    }
    return configs;
  }
  
  // Get fields for current source type
  protected readonly currentFields = computed(() => {
    return this.sourceTypeConfigs[this.sourceType()]?.fields ?? [];
  });
  
  // Get credential fields for current source type
  protected readonly credentialFields = computed(() => {
    return this.currentFields().filter(f => f.section === 'credentials');
  });
  
  // Get config fields for current source type
  protected readonly configFields = computed(() => {
    return this.currentFields().filter(f => f.section === 'config');
  });
  
  // Helper method to get field value as string (for input/select fields)
  protected getFieldValue(key: string): string {
    const value = this.simpleFieldValues()[key];
    return value !== undefined ? String(value) : '';
  }
  
  // Helper method to get field value as boolean (for checkbox fields)
  protected getFieldChecked(key: string, defaultValue: string | number | boolean | undefined): boolean {
    const value = this.simpleFieldValues()[key];
    if (value !== undefined) {
      return Boolean(value);
    }
    // Convert defaultValue to boolean if it's not already
    if (typeof defaultValue === 'boolean') {
      return defaultValue;
    }
    return false;
  }

  protected readonly sourceTypeOptions: SourceTypeOption[] = [
    { value: 'telegram', label: 'Telegram', icon: 'telegram', description: 'Receive messages from Telegram bots' },
    { value: 'webhook', label: 'Webhook', icon: 'webhook', description: 'Receive HTTP POST requests' },
    { value: 'whatsapp', label: 'WhatsApp', icon: 'whatsapp', description: 'Connect via WhatsApp Business API' },
    { value: 'discord', label: 'Discord', icon: 'discord', description: 'Receive messages from Discord bots' },
    { value: 'slack', label: 'Slack', icon: 'slack', description: 'Connect via Slack Socket Mode with real-time messaging' }
  ];

  constructor(
    private dialogRef: MatDialogRef<EditSourceModalComponent>,
    @Inject(MAT_DIALOG_DATA) public data: { source: Source },
    private api: ApiService
  ) {}

  ngOnInit(): void {
    // Populate form fields from existing source (except simple fields which need agents)
    const source = this.data.source;
    this.sourceId.set(source.source_id);
    this.sourceType.set(source.source_type);
    this.name.set(source.name);
    this.enabled.set(source.enabled);
    
    // Set JSON fields
    if (source.config && Object.keys(source.config).length > 0) {
      this.configJson.set(JSON.stringify(source.config, null, 2));
    }
    
    // Load agents first, then populate simple fields (agent dropdown needs options)
    this.loadAgents(() => {
      this.populateSimpleFieldsFromSource(source);
    });
  }
  
  private loadAgents(onLoaded?: () => void): void {
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
        // Populate simple fields after agents are loaded so dropdown has options
        onLoaded?.();
      },
      error: (err) => {
        console.error('Failed to load agents:', err);
        // Still try to populate fields even if agents failed to load
        onLoaded?.();
      }
    });
  }
  
  private populateSimpleFieldsFromSource(source: Source): void {
    const config = source.config || {};
    const values: Record<string, string | number | boolean> = {};
    
    const fields = this.sourceTypeConfigs[source.source_type]?.fields ?? [];
    
    for (const field of fields) {
      // Check config first
      if (config[field.key] !== undefined) {
        const configValue = config[field.key];
        // Only set if it's a valid type for our form
        if (typeof configValue === 'string' || typeof configValue === 'number' || typeof configValue === 'boolean') {
          values[field.key] = configValue;
        }
      }
      // Apply defaults if not set
      else if (field.defaultValue !== undefined && field.type !== 'password') {
        values[field.key] = field.defaultValue;
      }
    }
    
    this.simpleFieldValues.set(values);
  }

  protected resetForm(): void {
    this.sourceId.set('');
    this.sourceType.set('telegram');
    this.name.set('');
    this.configJson.set('');
    this.credentialsJson.set('');
    this.enabled.set(true);
    this.error.set(null);
    this.configTab.set('simple');
    this.simpleFieldValues.set({});
  }
  
  // Handle simple field value changes
  protected onSimpleFieldChange(key: string, event: Event): void {
    const target = event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
    const field = this.currentFields().find(f => f.key === key);
    
    let value: string | number | boolean;
    if (field?.type === 'checkbox') {
      value = (target as HTMLInputElement).checked;
    } else if (field?.type === 'number') {
      value = target.value ? parseInt(target.value, 10) : '';
    } else {
      value = target.value;
    }
    
    this.simpleFieldValues.update(values => ({
      ...values,
      [key]: value
    }));
  }
  
  // Switch between tabs
  protected onConfigTabChange(tab: ConfigTab): void {
    if (tab === 'json' && this.configTab() === 'simple') {
      // Converting from simple to JSON - populate JSON fields
      this.syncSimpleToJson();
    }
    this.configTab.set(tab);
  }
  
  // Sync simple field values to JSON format
  private syncSimpleToJson(): void {
    const values = this.simpleFieldValues();
    const fields = this.currentFields();
    
    const config: Record<string, unknown> = {};
    const credentials: Record<string, unknown> = {};
    
    for (const field of fields) {
      const value = values[field.key];
      if (value !== undefined && value !== '' && value !== field.defaultValue) {
        if (field.section === 'config') {
          config[field.key] = value;
        } else {
          credentials[field.key] = value;
        }
      }
    }
    
    this.configJson.set(Object.keys(config).length > 0 ? JSON.stringify(config, null, 2) : '');
    this.credentialsJson.set(Object.keys(credentials).length > 0 ? JSON.stringify(credentials, null, 2) : '');
  }

  protected handleClose(): void {
    this.resetForm();
    this.dialogRef.close();
  }

  protected onNameChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.name.set(target.value);
  }

  protected handleSubmit(): void {
    const nameValue = this.name();
    
    // Clear previous error
    this.error.set(null);
    
    // Validation - Name
    if (!nameValue.trim()) {
      this.error.set('Name is required');
      return;
    }
    if (nameValue.length > 128) {
      this.error.set('Name must be 128 characters or less');
      return;
    }

    let config: Record<string, unknown> = {};
    let credentials: Record<string, unknown> = {};

    if (this.configTab() === 'simple') {
      // Use simple field values
      const values = this.simpleFieldValues();
      const fields = this.currentFields();
      
      // Build config and credentials from simple fields
      // Only include fields that have values (not empty password fields)
      for (const field of fields) {
        const value = values[field.key];
        // Skip empty password fields (user wants to keep existing)
        if (field.type === 'password' && (value === undefined || value === '')) {
          continue;
        }
        if (value !== undefined && value !== '') {
          if (field.section === 'config') {
            config[field.key] = value;
          } else {
            credentials[field.key] = value;
          }
        }
      }
    } else {
      // Use JSON input
      if (this.configJson().trim()) {
        try {
          config = JSON.parse(this.configJson());
        } catch {
          this.error.set('Invalid JSON in Config field');
          return;
        }
      }

      if (this.credentialsJson().trim()) {
        try {
          credentials = JSON.parse(this.credentialsJson());
        } catch {
          this.error.set('Invalid JSON in Credentials field');
          return;
        }
      }
    }

    const sourceUpdate: SourceUpdate = {
      name: nameValue.trim(),
      config: Object.keys(config).length > 0 ? config : undefined,
      credentials: Object.keys(credentials).length > 0 ? credentials : undefined,
      enabled: this.enabled()
    };
    
    console.log('Closing dialog with source update:', sourceUpdate);
    this.dialogRef.close(sourceUpdate);
  }

  protected async handleTestConnection(): Promise<void> {
    // Build config and credentials from current form state
    let config: Record<string, unknown> = {};
    let credentials: Record<string, unknown> = {};

    if (this.configTab() === 'simple') {
      const values = this.simpleFieldValues();
      const fields = this.currentFields();
      
      for (const field of fields) {
        const value = values[field.key];
        if (field.type === 'password' && (value === undefined || value === '')) {
          continue;
        }
        if (value !== undefined && value !== '') {
          if (field.section === 'config') {
            config[field.key] = value;
          } else {
            credentials[field.key] = value;
          }
        }
      }
    } else {
      // Use JSON input
      if (this.configJson().trim()) {
        try {
          config = JSON.parse(this.configJson());
        } catch {
          this.testResult.set({ success: false, message: 'Invalid JSON in Config field' });
          return;
        }
      }

      if (this.credentialsJson().trim()) {
        try {
          credentials = JSON.parse(this.credentialsJson());
        } catch {
          this.testResult.set({ success: false, message: 'Invalid JSON in Credentials field' });
          return;
        }
      }
    }

    this.isTesting.set(true);
    this.testResult.set(null);

    try {
      const response = await this.api.testSource({
        source_type: this.sourceType(),
        config,
        credentials
      }).toPromise();
      
      this.testResult.set({
        success: response?.success ?? false,
        message: response?.message ?? 'Test completed'
      });
    } catch (err) {
      this.testResult.set({
        success: false,
        message: err instanceof Error ? err.message : 'Connection test failed'
      });
    } finally {
      this.isTesting.set(false);
    }
  }

  protected onConfigJsonChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.configJson.set(target.value);
  }

  protected onCredentialsJsonChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.credentialsJson.set(target.value);
  }

  protected onEnabledChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.enabled.set(target.checked);
  }

  protected isSubmitDisabled(): boolean {
    return this.isLoading() || !this.name().trim();
  }
}
