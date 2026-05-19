{
  "cases": [
    {
      "case_id": "telapp_axle_integration_flow",
      "repo_key": "telapp",
      "seed_files": ["tas/app/services/cron_service.rb"],
      "query": "How does Axle integration work?",
      "expected_top_files": [
        "tas/db/migrate/20260119150051_add_axle_id_to_provider_schedule_table.rb",
        "tas/app/controllers/consult_queues_controller.rb",
        "tas/app/models/provider_schedule.rb",
        "tas/app/services/axle_health_authenticatable.rb",
        "tas/app/services/axle_visit_creator.rb"
      ],
      "tier": 2,
      "token_budget": 8000,
      "max_depth": 1,
      "notes": "Key Axle files: authenticatable, visit creator, migration, queue controller. Tests discovery without full hub expansion."
    },
    {
      "case_id": "telapp_consultation_flow",
      "repo_key": "telapp",
      "seed_files": ["tas/app/models/consultation.rb"],
      "query": "How are consultations handled?",
      "expected_top_files": [
        "tas/app/controllers/consultations_controller.rb",
        "tas/app/services/consultation_service.rb",
        "tas/app/models/patient.rb",
        "tas/app/models/provider.rb",
        "tas/db/migrate/20200101000000_create_consultations.rb"
      ],
      "tier": 2,
      "token_budget": 12000,
      "max_depth": 2,
      "notes": "Core consultation domain: controllers, services, models, migrations. Tests broader co-change patterns."
    },
    {
      "case_id": "telapp_patient_controller_endpoint",
      "repo_key": "telapp",
      "seed_files": ["tas/app/controllers/patients_controller.rb"],
      "query": "patient API endpoint implementation",
      "expected_top_files": [
        "tas/app/models/patient.rb",
        "tas/app/serializers/patient_serializer.rb",
        "tas/config/routes.rb",
        "tas/spec/controllers/patients_controller_spec.rb",
        "tas/app/services/patient_service.rb"
      ],
      "tier": 2,
      "token_budget": 10000,
      "max_depth": 1,
      "notes": "API layer: controller, model, serializer, routes, tests. Narrow co-change graph."
    },
    {
      "case_id": "telapp_authentication_mechanism",
      "repo_key": "telapp",
      "seed_files": ["tas/app/models/user.rb"],
      "query": "user authentication and session management",
      "expected_top_files": [
        "tas/config/initializers/devise.rb",
        "tas/app/controllers/sessions_controller.rb",
        "tas/app/services/auth_service.rb",
        "tas/spec/models/user_spec.rb"
      ],
      "tier": 2,
      "token_budget": 10000,
      "max_depth": 2,
      "notes": "Auth cross-section: model, config, controller, service. Tests feature-area discovery."
    },
    {
      "case_id": "telapp_provider_availability_scheduling",
      "repo_key": "telapp",
      "seed_files": ["tas/app/models/provider_schedule.rb"],
      "query": "how provider availability and scheduling works",
      "expected_top_files": [
        "tas/app/services/schedule_service.rb",
        "tas/app/controllers/provider_schedules_controller.rb",
        "tas/db/migrate/20190101000000_create_provider_schedules.rb",
        "tas/app/models/provider.rb",
        "tas/spec/services/schedule_service_spec.rb"
      ],
      "tier": 2,
      "token_budget": 10000,
      "max_depth": 2,
      "notes": "Domain model with services and API: tests coupling of data + business logic + presentation."
    },
    {
      "case_id": "telapp_payment_processing",
      "repo_key": "telapp",
      "seed_files": ["tas/app/services/payment_processor.rb"],
      "query": "payment transaction processing and reconciliation",
      "expected_top_files": [
        "tas/app/models/payment.rb",
        "tas/app/models/transaction.rb",
        "tas/app/services/stripe_integration.rb",
        "tas/db/migrate/20200601000000_create_payments.rb",
        "tas/spec/services/payment_processor_spec.rb"
      ],
      "tier": 2,
      "token_budget": 10000,
      "max_depth": 2,
      "notes": "Financial domain: processor, models, 3rd-party integration. Tests multi-file dependency chains."
    },
    {
      "case_id": "telapp_notification_system",
      "repo_key": "telapp",
      "seed_files": ["tas/app/services/notification_service.rb"],
      "query": "how notifications are sent to users",
      "expected_top_files": [
        "tas/app/models/notification.rb",
        "tas/app/mailers/user_mailer.rb",
        "tas/app/services/twilio_integration.rb",
        "tas/config/sidekiq.yml",
        "tas/app/jobs/send_notification_job.rb"
      ],
      "tier": 2,
      "token_budget": 10000,
      "max_depth": 2,
      "notes": "Async system: service, models, mailer, background jobs, config. Tests job queue integration."
    },
    {
      "case_id": "telapp_reporting_analytics",
      "repo_key": "telapp",
      "seed_files": ["tas/app/services/report_generator.rb"],
      "query": "user and session analytics reporting",
      "expected_top_files": [
        "tas/app/models/analytics_event.rb",
        "tas/lib/analytics/tracker.rb",
        "tas/config/analytics.yml",
        "tas/spec/services/report_generator_spec.rb"
      ],
      "tier": 1,
      "token_budget": 8000,
      "max_depth": 1,
      "notes": "Analytics infrastructure: models, tracker lib, config. Tests library-level integration."
    }
  ]
}
